# Use MorphoPilot

MorphoPilot lets you turn a large 3D image into manageable annotation tiles, review each mask, train a category-specific model and reconstruct the full mask. Your server keeps the saved data between sessions.

## Start a dataset

1. Open **Data Library** and choose Neurite, Spot, Cell or Organ. You can also create a category with your own name.
2. Choose **Upload a new volume**, select the category and upload a single-channel 3D TIFF, NPY or NPZ image (`image` key). Array order must be Z,Y,X.
3. Review the tile shape. The default is **25 Z × 192 Y × 192 X**; edge tiles keep their actual smaller dimensions. The source pixels are not resized or discarded.
4. Wait for import to finish, then choose **Load**. Select individual numbered tiles to annotate.

A fresh installation has no uploaded data, prior annotations or trained category models. To try the workflow without your own image, run the synthetic-data generator described in `DEPLOYMENT.md` and upload an image whose name does not end in `_reference.tif`.

## Annotate and review

- Scroll over the image to move through Z. Use the Z slider or number field for precise navigation. The complete XY tile stays in view.
- Adjust brightness and contrast for viewing; this does not change stored training pixels.
- On a GPU installation, place a positive prompt on the intended structure. Place a negative prompt on a clearly unwanted region, avoiding the structure you want to keep. Points, boxes, scribbles and lassos drive the interactive segmentation model.
- In CPU mode, use manual paint/erase tools. The same tools are available on GPU installations for corrections.
- Inspect all slices affected by the prediction. Undo reverses the previous edit. Saving an edit is distinct from confirming that the whole tile has been reviewed.
- Use **Confirm** only when the tile mask is acceptable. An empty tile requires the explicit reviewed-empty-background choice. Constant images can still be reviewed and merged but are excluded from neural training.

The full-image preview distinguishes saved masks from unreviewed tiles. A visible saved prediction does not automatically mean that tile has been confirmed. Orange unreviewed areas help you find work that still needs review.

## Train the current category

Choose **Fine-tune Prompt** after confirming one or more useful masks. The default training scope includes confirmed, trainable tiles from every uploaded image in the selected category. Other categories and evaluation-only samples do not enter this training set.

Each completed round inherits the current category model and becomes its new current model. Saved masks stay unchanged until you explicitly edit or run inference again. Upload another image or choose an unconfirmed tile to assess the new model. A lower training loss alone does not establish accuracy on new data; compare against an independent reference mask where available.

New categories begin with the original interactive segmentation checkpoint. Category isolation concerns stored weights and training data; workers share the physical GPU and run GPU tasks one at a time.

## Use Auto mode

After Prompt fine-tuning, create the Auto stem. This initializes an image-only input path from the current Prompt model. **Creating the stem does not mean Auto training has happened.**

Train Auto using reviewed image–mask pairs, then use **Run Auto** on an unconfirmed tile to obtain a mask without manual prompts. Review that result before confirming it. Auto training updates the shared network as well as the Auto stem; evaluate Prompt behavior again if you continue using both modes. Returning to Prompt fine-tuning marks Auto inactive until it is prepared again.

## View and export the whole image

Return to Data Library and choose the full-volume visualization to browse the original image and saved tile masks together. The viewer places each tile at its original coordinates.

Confirm every tile before creating a final merged mask. A **draft** merge is available for partial work and is labelled accordingly. Editing or reopening a tile makes an earlier merge stale; merge again before exporting. The exported TIFF keeps the source Z,Y,X dimensions.

## Switch, delete and resume

- Loading another volume changes the working selection and preserves saved images, masks and prompts.
- Each category maintains an independent current model. Data from Spot does not train Neurite unless you deliberately upload it into Neurite.
- Delete removes the entire uploaded volume and its tiles from the active library. This is recoverable Trash, so disk space is retained until an administrator manages backups/storage.
- Restore from Trash brings the volume back with its saved annotations. Existing trained models and their immutable training snapshots are retained when a volume is deleted.
- Restarting the server preserves completed work. An interrupted job is reported as failed, rather than falsely marked complete.

The included synthetic fixtures and example screenshots demonstrate interaction and storage. They do not establish medical validity, clinical readiness or performance on a new imaging domain.
