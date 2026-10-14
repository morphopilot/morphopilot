#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create deterministic synthetic 3D input images and separate reference masks.

These procedural fixtures are NOT biological acquisitions, model predictions,
or evidence of segmentation performance. Dependencies: numpy, tifffile, Pillow.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import tifffile
from PIL import Image, ImageDraw, ImageFont

SEED = 20261013
SHAPE = (25, 192, 192)
NEURITE_SHAPE = (25, 384, 384)
SUPPORT_RADIUS_SQUARED = 4.0
BRIGHT_SIGNAL_THRESHOLD = 400
SPOT_OVERLAY_COLOR = (255, 120, 31)
SPOT_OVERLAY_OPACITY = .92

def stamp(field, truth, z, y, x, rz, rxy, amplitude):
    """Render a compact Gaussian whose complete support is its reference label.

    A non-truncated Gaussian left visible halos outside the original ellipsoid
    mask. Subtracting the boundary value makes the signal fade smoothly to zero
    exactly at the geometric support, with no unlabelled synthetic structures.
    """
    z0,z1=max(0,int(z-3*rz)),min(field.shape[0],int(z+3*rz)+2)
    y0,y1=max(0,int(y-3*rxy)),min(field.shape[1],int(y+3*rxy)+2)
    x0,x1=max(0,int(x-3*rxy)),min(field.shape[2],int(x+3*rxy)+2)
    if z0>=z1 or y0>=y1 or x0>=x1:return
    zz,yy,xx=np.ogrid[z0:z1,y0:y1,x0:x1]
    q=((zz-z)/rz)**2+((yy-y)/rxy)**2+((xx-x)/rxy)**2
    sl=np.s_[z0:z1,y0:y1,x0:x1]
    inside = q < SUPPORT_RADIUS_SQUARED
    floor = np.exp(-SUPPORT_RADIUS_SQUARED / 2)
    primitive = np.where(inside, amplitude * (np.exp(-q/2)-floor)/(1-floor), 0)
    np.maximum(field[sl],primitive,out=field[sl])
    truth[sl] |= inside

def neurites(shape=SHAPE,seed=SEED,roots=14):
    rng=np.random.default_rng(seed)
    signal=np.zeros(shape,np.float32);truth=np.zeros(shape,bool)
    h,w=shape[1:]
    def path(x,y,z,angle,length,width,phase,branch=True):
        a=angle
        coords=[]
        for k in range(length):
            a += .014*np.sin(k*.045+phase)+rng.normal(0,.027)
            x+=np.cos(a)*.74;y+=np.sin(a)*.74
            zz=z+2.6*np.sin(k*.037+phase)
            if x < -8 or x > w+8 or y < -8 or y > h+8:break
            amp=2500*(.82+.18*np.sin(k*.12+phase))
            stamp(signal,truth,zz,y,x,1.45,width,amp)
            coords.append((x,y,zz,a))
        if branch and len(coords)>45:
            for frac,turn in [(0.38,.72),(0.70,-.65)]:
                xx,yy,zz,aa=coords[int(len(coords)*frac)]
                path(xx,yy,zz,aa+turn,max(40,int(length*.45)),width*.72,phase+1.3,False)
    for i in range(roots):
        # A deterministic mix of internal fibers and edge-crossing fibers.
        x=rng.uniform(0,w);y=rng.uniform(0,h)
        z=rng.uniform(4,shape[0]-5);angle=rng.uniform(0,2*np.pi)
        path(x,y,z,angle,int(rng.uniform(.5,1.05)*max(h,w)),rng.uniform(.72,1.30),rng.uniform(0,6.28))
    # Keep meaningful anatomy-like geometry visible in every comparison frame,
    # including the first and last Z planes. These are real 3D fibers rather
    # than a repeated 2D mask; each path changes its depth along its length.
    for i,z in enumerate(np.linspace(0,shape[0]-1,5)):
        path(w*.49,h*.49,z,.35+i*1.13,150,1.1,.6+i*.9)
    return add_background(signal,rng),truth.astype(np.uint8)

def spots(shape=SHAPE,seed=SEED+1):
    rng=np.random.default_rng(seed);signal=np.zeros(shape,np.float32);truth=np.zeros(shape,bool)
    # Sample visible puncta across a real 3D coordinate system.
    for i in range(100):
        stamp(signal,truth,rng.uniform(2,shape[0]-3),rng.uniform(5,shape[1]-6),rng.uniform(5,shape[2]-6),rng.uniform(.9,1.35),rng.uniform(.95,1.65),rng.uniform(1300,3200))
    # Explicit boundary puncta ensure both ends of the Z control contain
    # visible, labelled objects, rather than only the tails of interior spots.
    for z in (0,shape[0]-1):
        for y,x in ((.23,.28),(.51,.60),(.76,.43),(.39,.82)):
            stamp(signal,truth,z,y*shape[1],x*shape[2],1.3,1.55,2500)
    return add_background(signal,rng),truth.astype(np.uint8)

def add_background(signal,rng):
    zz,yy,xx=np.ogrid[:signal.shape[0],:signal.shape[1],:signal.shape[2]]
    background=110+27*np.sin(xx/26)+19*np.cos(yy/31)+9*np.sin(zz/5)
    noise=rng.normal(0,25,signal.shape).astype(np.float32)
    shot=rng.normal(0,1,signal.shape).astype(np.float32)*np.sqrt(signal+background)
    return np.clip(signal+background+noise+shot,0,65535).round().astype(np.uint16)

def file_sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def grey(raw):return np.clip((raw.astype(np.float32)-95)/2300,0,1)**.72

def overlay(g,mask,color=(182,242,120),opacity=.88):
    rgb=np.repeat((g*255).round().astype('uint8')[...,None],3,-1)
    selected=mask>0
    rgb[selected]=(rgb[selected]*(1-opacity)+np.array(color)*opacity).round().astype('uint8')
    return rgb

def coverage_report(raw,truth):
    """Check that every bright structure and every Z frame has a reference."""
    bright = raw >= BRIGHT_SIGNAL_THRESHOLD
    missing = bright & ~truth.astype(bool)
    if missing.any():
        raise ValueError(f'{int(missing.sum())} bright voxels have no reference mask')
    foreground = truth.reshape(len(truth),-1).sum(axis=1)
    if not np.all(foreground > 0):
        raise ValueError('Every Z plane must include reference foreground')
    return {'bright_threshold_uint16':BRIGHT_SIGNAL_THRESHOLD,
            'bright_voxels':int(bright.sum()),'unmasked_bright_voxels':0,
            'reference_voxels_per_z':[int(v) for v in foreground]}

def render_assets(out,neurite,neurite_truth,spot,spot_truth):
    out.mkdir(parents=True,exist_ok=True)
    neurite = neurite[:,96:288,96:288]
    neurite_truth = neurite_truth[:,96:288,96:288]
    ng=grey(neurite);sg=grey(spot)
    coverage={'neurite':coverage_report(neurite,neurite_truth),'spot':coverage_report(spot,spot_truth)}
    z=12;nz=neurite.shape[0]
    frame_w=frame_h=192;cols=5;rows=5
    pairs=[('neurite-raw.webp',(ng[z]*255).astype('uint8')),('neurite-mask.webp',overlay(ng[z],neurite_truth[z])),('spot-raw.webp',(sg[z]*255).astype('uint8')),('spot-mask.webp',overlay(sg[z],spot_truth[z],SPOT_OVERLAY_COLOR,SPOT_OVERLAY_OPACITY))]
    for name,frame in pairs:
        Image.fromarray(frame).resize((768,768),Image.Resampling.LANCZOS).save(out/name,lossless=True)
    for name,frames,mode in [('stack-raw.webp',(ng*255).astype('uint8'),'L'),('stack-mask.webp',overlay(ng,neurite_truth),'RGB'),('spot-stack-raw.webp',(sg*255).astype('uint8'),'L'),('spot-stack-mask.webp',overlay(sg,spot_truth,SPOT_OVERLAY_COLOR,SPOT_OVERLAY_OPACITY),'RGB')]:
        atlas=Image.new(mode,(cols*192,rows*192))
        for i,frame in enumerate(frames):atlas.paste(Image.fromarray(frame),((i%cols)*192,(i//cols)*192))
        atlas.save(out/name,lossless=True)
    full,full_truth=neurites((25,448,512),SEED+2,roots=36)
    mip=grey(full.max(axis=0));rgb=(np.stack([mip*.77,mip,mip*.77],axis=-1)*255).clip(0,255).astype('uint8')
    Image.fromarray(rgb).save(out/'hero-neurites.webp',lossless=True)
    Image.fromarray(rgb).save(out/'hero.png')
    stack={'label':'SYNTHETIC procedural neurite fixture','synthetic':True,'is_model_prediction':False,'initial_z':z,'frame_count':nz,'frame_width':192,'frame_height':192,'columns':cols,'rows':rows,'shape_zyx':list(SHAPE),'raw_sprite':'assets/stack-raw.webp','mask_sprite':'assets/stack-mask.webp','spot_raw_sprite':'assets/spot-stack-raw.webp','spot_mask_sprite':'assets/spot-stack-mask.webp','coverage':coverage,'prompts':[],'caption':'Procedurally generated input and complete known reference labels. All 25 Z slices include labelled structures. This interaction does not run a segmentation model.'}
    # Illustrative markers are placed directly from known synthetic truth; not model prompts.
    for k in range(nz):
        ys,xs=np.where(neurite_truth[k]>0)
        if len(xs):
            index=int(np.argmin((ys-96)**2+(xs-96)**2));y,x=int(ys[index]),int(xs[index]);stack['prompts'].append({'z':k,'y':y,'x':x,'positive':True})
            for dy,dx in [(12,0),(0,12),(-12,0),(0,-12),(20,10)]:
                yy,xx=y+dy,x+dx
                if 1<=yy<191 and 1<=xx<191 and not neurite_truth[k,yy-1:yy+2,xx-1:xx+2].any() and neurite[k,yy,xx]<320:stack['prompts'].append({'z':k,'y':yy,'x':xx,'positive':False});break
    (out/'stack.json').write_text(json.dumps(stack,indent=2)+'\n')
    (out/'stack-data.js').write_text('// SPDX-License-Identifier: Apache-2.0\nwindow.MORPHOPILOT_STACK = '+json.dumps(stack,indent=2)+';\n')
    render_figure(out,ng[z],neurite_truth[z])
    render_training_pairs(out,ng[z],neurite_truth[z])

def illustration_font(size):
    for path in ['/System/Library/Fonts/Supplemental/Arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(path).exists():return ImageFont.truetype(path,size)
    return ImageFont.load_default()

def diagram_arrow(draw,start,end,color='#b6f278',width=4):
    draw.line((start,end),fill=color,width=width)
    angle=np.arctan2(end[1]-start[1],end[0]-start[0])
    tip=np.array(end);back=tip-12*np.array([np.cos(angle),np.sin(angle)])
    side=6*np.array([-np.sin(angle),np.cos(angle)])
    draw.polygon([tuple(tip),tuple(back+side),tuple(back-side)],fill=color)

def render_training_pairs(out,raw,truth):
    """Conceptual training inputs and optimization, not a predicted output."""
    im=Image.new('RGB',(1600,900),'#0a1011');d=ImageDraw.Draw(im);font=illustration_font
    d.text((64,47),'Teach the model with reviewed pairs.',font=font(47),fill='#edf4e8')
    d.text((66,118),'The image and binary annotation become training inputs.',font=font(25),fill='#aabca3')
    d.rounded_rectangle((58,190,740,693),20,fill='#131d1b',outline='#344732',width=2)
    d.text((91,220),'01   CONFIRMED SAMPLE',font=font(22),fill='#b6f278')
    raw_image=Image.fromarray((raw*255).astype('uint8')).convert('RGB').resize((278,278))
    label_image=Image.fromarray((truth*255).astype('uint8')).convert('RGB').resize((278,278),Image.Resampling.NEAREST)
    im.paste(raw_image,(91,278));im.paste(label_image,(428,278));d=ImageDraw.Draw(im)
    d.text((151,580),'Raw image',font=font(25),fill='#edf4e8')
    d.text((476,580),'Binary mask',font=font(25),fill='#edf4e8')
    d.text((91,642),'Reviewed foreground labels',font=font(21),fill='#aabca3')
    d.text((379,392),'+',font=font(42),fill='#b6f278')
    diagram_arrow(d,(765,437),(826,437),width=5)
    d.rounded_rectangle((849,190,1542,693),20,fill='#131d1b',outline='#344732',width=2)
    d.text((885,220),'02   OPTIMIZE MODEL WEIGHTS',font=font(22),fill='#b6f278')
    nodes=[[(942,y) for y in (315,395,475)],[(1149,y) for y in (315,395,475)],[(1356,y) for y in (315,395,475)]]
    for left,right in zip(nodes,nodes[1:]):
        for a in left:
            for b in right:d.line((a,b),fill='#415b38',width=3)
    for layer in nodes:
        for cx,cy in layer:d.ellipse((cx-17,cy-17,cx+17,cy+17),fill='#b6f278',outline='#dff9c3',width=2)
    d.text((986,529),'Prompt model',font=font(30),fill='#edf4e8')
    d.rounded_rectangle((885,588,1505,658),12,fill='#233720',outline='#668f4b',width=2)
    d.text((941,610),'Loss → gradients → updated weights',font=font(24),fill='#ddf5cf')
    d.text((66,753),'Training updates the weights. Review inspects complete masks.',font=font(28),fill='#edf4e8')
    d.text((66,825),'CONCEPTUAL SCHEMATIC · Synthetic input / known reference · No model is run in this preview',font=font(21),fill='#90a28a')
    im.save(out/'training-pairs.png')

def render_figure(out,raw,truth):
    """A clearly labelled workflow schematic, never a fabricated app screenshot."""
    im=Image.new('RGB',(1600,740),'#0a1011');d=ImageDraw.Draw(im)
    font=illustration_font
    d.text((58,38),'MorphoPilot',font=font(42),fill='#ecf2e9');d.text((60,99),'TEACH THE MODEL. REVEAL THE STRUCTURE.',font=font(19),fill='#b6f278')
    titles=['01  UPLOAD','02  GUIDE','03  TRAIN','04  REVIEW'];sub=['A synthetic 3D volume','Illustrative prompt positions','Paired inputs update the weights','Inspect the complete reference mask']
    img=Image.fromarray((raw*255).astype('uint8')).convert('RGB').resize((338,338));ov=Image.fromarray(overlay(raw,truth)).resize((338,338))
    for i in range(4):
        x=60+i*385;d.rounded_rectangle((x-10,170,x+348,625),10,fill='#131d1b',outline='#314433',width=1)
        d.text((x+10,184),titles[i],font=font(17),fill='#b6f278')
        if i!=2:
            im.paste(ov if i==3 else img,(x,225))
        else:
            im.paste(img.resize((146,146)),(x+6,233))
            binary=Image.fromarray((truth*255).astype('uint8')).convert('RGB').resize((146,146),Image.Resampling.NEAREST)
            im.paste(binary,(x+184,233));d=ImageDraw.Draw(im)
            d.text((x+25,391),'Raw image',font=font(19),fill='#edf4e8')
            d.text((x+200,391),'Binary mask',font=font(19),fill='#edf4e8')
            diagram_arrow(d,(x+79,419),(x+138,452),width=3)
            diagram_arrow(d,(x+257,419),(x+200,452),width=3)
            d.rounded_rectangle((x+61,456,x+277,515),8,fill='#233720',outline='#6b9650',width=2)
            d.text((x+95,471),'TRAIN MODEL',font=font(20),fill='#b6f278')
            d.text((x+81,535),'Optimize weights',font=font(21),fill='#edf4e8')
        d.text((x+4,586),sub[i],font=font(15),fill='#c5d6c0')
    d=ImageDraw.Draw(im)
    ys,xs=np.where(truth>0)
    if len(xs):
        k=int(np.argmin((ys-96)**2+(xs-96)**2));py,px=int(ys[k]),int(xs[k])
        points=[(py,px,'P','#b6f278')]
        by,bx=np.where((truth==0)&(raw<.08));far=(by-py)**2+(bx-px)**2;valid=np.where((far>144)&(far<625))[0]
        if len(valid):j=valid[np.argmin(far[valid])];points.append((int(by[j]),int(bx[j]),'N','#ff9378'))
        for yy,xx,label,color in points:
            cx,cy=445+xx*338/192,225+yy*338/192
            d.ellipse((cx-8,cy-8,cx+8,cy+8),outline=color,width=2)
            d.text((cx+11,cy-16),label,font=font(19),fill=color)
    d.text((60,675),'SYNTHETIC DATA · Workflow schematic · Reference labels, not neural predictions',font=font(18),fill='#90a28a')
    im.save(out/'workflow-figure.png')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=Path(__file__).resolve().parent/'generated')
    parser.add_argument('--website-assets',type=Path,default=None,help='Optionally regenerate the landing-page scientific illustrations.')
    args=parser.parse_args();out=args.output_dir;out.mkdir(parents=True,exist_ok=True)
    n,nt=neurites(NEURITE_SHAPE,roots=28);s,st=spots();manifest=[]
    coverage={'neurite':coverage_report(n,nt),'spot':coverage_report(s,st)}
    for name,array,role in [('neurites.tif',n,'input'),('neurites_reference.tif',nt,'known procedural reference; NOT model output'),('spots.tif',s,'input'),('spots_reference.tif',st,'known procedural reference; NOT model output')]:
        p=out/name;tifffile.imwrite(p,array,photometric='minisblack',metadata={'axes':'ZYX','synthetic':True,'description':'Generated MorphoPilot test fixture; not a biological acquisition or model prediction.'},compression='deflate')
        manifest.append({'file':name,'role':role,'synthetic':True,'shape_zyx':list(array.shape),'dtype':str(array.dtype),'sha256':file_sha(p),'array_sha256':hashlib.sha256(array.tobytes()).hexdigest(),'bytes':p.stat().st_size})
    meta={'license':'Apache-2.0','synthetic':True,'numpy_version':np.__version__,'tifffile_version':tifffile.__version__,'seed':SEED,'generator':'examples/generate_synthetic.py','neurite_shape_zyx':list(NEURITE_SHAPE),'spot_shape_zyx':list(SHAPE),'purpose':'Reproducible upload/tiling/rendering tests, not a segmentation-performance benchmark.','generation':'Compact anisotropic Gaussian tube/spot primitives, complete geometric support masks, seeded additive noise. Boundary structures ensure foreground in every Z plane. No biological acquisitions or trained models used.','coverage':coverage,'reproducibility':'Array values are deterministic for a fixed NumPy version. TIFF/WebP bytes may differ across library versions.','files':manifest}
    (out/'manifest.json').write_text(json.dumps(meta,indent=2)+'\n')
    if args.website_assets:
        render_assets(args.website_assets,n,nt,s,st)
        provenance={'license':'Apache-2.0','synthetic':True,'is_model_prediction':False,'seed':SEED,'generator':'examples/generate_synthetic.py','neurite_fixture_shape_zyx':list(NEURITE_SHAPE),'spot_fixture_shape_zyx':list(SHAPE),'comparison_crop_origin_zyx':[0,96,96],'comparison_shape_zyx':list(SHAPE),'hero_shape_zyx':[25,448,512],'checked_in_fixture_manifest':'examples/fixtures/manifest.json','checked_in_inputs':['examples/fixtures/neurites.tif','examples/fixtures/spots.tif'],'checked_in_references':['examples/fixtures/neurites_reference.tif','examples/fixtures/spots_reference.tif'],'rendering':'Known synthetic input/reference arrays. Fixed display range 95–2395, gamma=0.72. Hero maximum-intensity projection is tinted green. Reference overlays are lime/orange; the spot overlay uses #ff781f at 92% opacity. Training illustrations are conceptual image/binary-mask input pairs and optimization diagrams. No real research images, weights, or screenshots.','assets':[{'file':p.name,'sha256':file_sha(p)} for p in sorted(args.website_assets.iterdir()) if p.suffix in {'.webp','.png'}],'copy_note':'Examples demonstrate the interface, file format and anatomy-like geometry, not scientific or model accuracy.'}
        (args.website_assets/'source-notes.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print(json.dumps(meta,indent=2))
if __name__=='__main__':main()
