// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../website/app.js', import.meta.url), 'utf8');

// Exercise the shipped, dependency-free script with a small DOM/canvas boundary.
// Image completion and time are controllable, including file:// data URL loads.
function explorer({ cached = false } = {}) {
  const nodes = new Map(), images = [], intervals = new Map(), documentEvents = {};
  let nextTimer = 0;
  const makeNode = () => ({
    style: {}, attrs: {}, children: [], listeners: {}, dataset: {}, value: '',
    disabled: false, hidden: false, textContent: '', draws: [],
    classList: { toggle() {}, add() {}, remove() {} },
    append(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    getContext() {
      return {
        clearRect() {}, drawImage: (...args) => { this.draws.push(args); },
        beginPath() {}, arc() {}, stroke() {}, moveTo() {}, lineTo() {}, strokeText() {}, fillText() {},
      };
    },
    click() { this.listeners.click?.({ target: this }); },
    input(value) { this.value = String(value); this.listeners.input?.({ target: this }); },
  });
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, makeNode());
    return nodes.get(selector);
  };
  const comparisonButtons = [100, 52, 0].map(value => {
    const element = makeNode(); element.dataset.comparison = String(value); return element;
  });
  const datasetButtons = ['neurite', 'spot'].map(value => {
    const element = makeNode(); element.dataset.dataset = value; return element;
  });
  const stepButtons = [0, 1, 2, 3].map(value => {
    const element = makeNode(); element.dataset.step = String(value); return element;
  });
  node('#comparison-range').value = '52';
  const document = {
    hidden: false,
    body: makeNode(),
    querySelector: node,
    querySelectorAll(selector) {
      if (selector === '[data-comparison]') return comparisonButtons;
      if (selector === '[data-dataset]') return datasetButtons;
      if (selector === '[data-step]') return stepButtons;
      return [];
    },
    createElement: makeNode,
    addEventListener(name, fn) { documentEvents[name] = fn; },
  };
  class MockImage {
    constructor() { this.complete = false; this.naturalWidth = 0; images.push(this); }
    set src(value) { this.url = value; if (cached) this.load(); }
    load() { this.complete = true; this.naturalWidth = 960; this.onload?.(); }
    fail() { this.complete = true; this.naturalWidth = 0; this.onerror?.(); }
  }
  const stack = { initial_z: 12, frame_count: 25, columns: 5, frame_width: 192, frame_height: 192, prompts: [] };
  const window = {
    MORPHOPILOT_STACK: stack,
    MORPHOPILOT_ASSET_URLS: { 'assets/stack-raw.webp': 'data:image/webp;base64,fixture' },
    addEventListener() {},
  };
  vm.runInNewContext(source, {
    document, window, Image: MockImage, URL,
    location: { protocol: 'file:', hostname: '' },
    setInterval(fn) { const id = nextTimer++; intervals.set(id, fn); return id; },
    clearInterval(id) { intervals.delete(id); },
  });
  return {
    node, images, intervals, comparisonButtons, datasetButtons, stepButtons,
    canvases: [node('#raw-view').children[0], node('#mask-view').children[0]],
    ready() { images.forEach(image => image.load()); },
    tick() { [...intervals.values()].forEach(fn => fn()); },
    visibility(hidden) { document.hidden = hidden; documentEvents.visibilitychange(); },
  };
}

test('comparison has full original and full mask endpoints, plus a matching divider', () => {
  const demo = explorer();
  demo.comparisonButtons[2].click();
  assert.equal(demo.node('#mask-view').style.clipPath, 'inset(0 0 0 0%)');
  assert.equal(demo.node('#divider').hidden, true);
  assert.equal(demo.node('#raw-label').hidden, true);
  assert.equal(demo.node('#comparison-state').textContent, 'Full reference mask');
  demo.comparisonButtons[0].click();
  assert.equal(demo.node('#mask-view').style.clipPath, 'inset(0 0 0 100%)');
  assert.equal(demo.node('#mask-label').hidden, true);
  assert.equal(demo.node('#comparison-state').textContent, 'Original image only');
  demo.node('#comparison-range').input(34);
  assert.equal(demo.node('#mask-view').style.clipPath, 'inset(0 0 0 34%)');
  assert.equal(demo.node('#divider').style.left, '34%');
  assert.equal(demo.node('#divider').hidden, false);
  assert.equal(demo.node('#raw-label').hidden, false);
  assert.equal(demo.node('#mask-label').hidden, false);
});

test('first and last Z use the same atlas coordinates in original and overlay for both datasets', () => {
  const demo = explorer(); demo.ready();
  for (const [datasetIndex, button] of demo.datasetButtons.entries()) {
    button.click();
    for (const [z, coordinates] of [[0, [0, 0]], [24, [768, 768]]]) {
      demo.node('#z-range').input(z);
      assert.equal(demo.node('#z-output').textContent, `${String(z + 1).padStart(2, '0')} / 25`);
      demo.canvases.forEach((canvas, index) => {
        const draw = canvas.draws.at(-1);
        assert.equal(draw[0], demo.images[datasetIndex * 2 + index]);
        assert.deepEqual(draw.slice(1, 5), [...coordinates, 192, 192]);
      });
    }
  }
});

test('dataset captions and legend colors match lime neurite and orange spot references', () => {
  const demo = explorer(); demo.ready();
  assert.match(demo.node('#example-caption').textContent, /reference in lime green/);
  assert.equal(demo.node('#mask-legend-swatch').style.backgroundColor, '#b6f278');
  assert.equal(demo.node('#mask-legend-text').textContent, 'Reference mask · lime');
  demo.datasetButtons[1].click();
  assert.match(demo.node('#example-caption').textContent, /reference in orange/);
  assert.equal(demo.node('#mask-legend-swatch').style.backgroundColor, '#ff781f');
  assert.equal(demo.node('#mask-legend-text').textContent, 'Reference mask · orange');
});

test('play waits for both selected assets and cached offline data URLs are handled', () => {
  const demo = explorer();
  assert.equal(demo.node('#play-stack').disabled, true);
  demo.images[0].load();
  assert.equal(demo.node('#play-stack').disabled, true);
  demo.images[1].load();
  assert.equal(demo.node('#play-stack').disabled, false);
  assert.equal(demo.node('#play-stack').textContent, '▷ Play');
  const cached = explorer({ cached: true });
  assert.equal(cached.images[0].url, 'data:image/webp;base64,fixture');
  assert.equal(cached.node('#play-stack').disabled, false);
  assert.ok(cached.canvases.every(canvas => canvas.draws.length > 0));
});

test('play advances immediately, keeps advancing, and pause cancels timer even with ID zero', () => {
  const demo = explorer(); demo.ready();
  demo.node('#play-stack').click();
  assert.equal(demo.node('#z-output').textContent, '14 / 25');
  assert.equal(demo.node('#play-stack').textContent, 'Ⅱ Pause');
  assert.equal(demo.node('#play-stack').attrs['aria-pressed'], 'true');
  demo.tick();
  assert.equal(demo.node('#z-output').textContent, '15 / 25');
  demo.node('#play-stack').click();
  assert.equal(demo.intervals.size, 0);
  demo.tick();
  assert.equal(demo.node('#z-output').textContent, '15 / 25');
  assert.equal(demo.node('#play-stack').textContent, '▷ Play');
});

test('play wraps the final Z and manual navigation pauses at either endpoint', () => {
  const demo = explorer(); demo.ready();
  demo.node('#z-range').input(24);
  demo.node('#play-stack').click();
  assert.equal(demo.node('#z-output').textContent, '01 / 25');
  demo.node('#z-range').input(24);
  assert.equal(demo.intervals.size, 0);
  assert.equal(demo.node('#z-output').textContent, '25 / 25');
  demo.node('#z-range').input(0);
  assert.equal(demo.node('#z-output').textContent, '01 / 25');
});

test('failed assets disable only their dataset; switching to ready assets recovers', () => {
  const demo = explorer(); demo.ready();
  demo.node('#play-stack').click();
  demo.images[1].fail();
  assert.equal(demo.intervals.size, 0);
  assert.equal(demo.node('#play-stack').disabled, true);
  assert.match(demo.node('#example-caption').textContent, /could not load/);
  demo.datasetButtons[1].click();
  assert.equal(demo.node('#play-stack').disabled, false);
  demo.node('#play-stack').click();
  assert.equal(demo.intervals.size, 1);
});

test('leaving the browser tab pauses user playback; visibility notifications alone do not', () => {
  const demo = explorer(); demo.ready(); demo.node('#play-stack').click();
  demo.visibility(false);
  assert.equal(demo.intervals.size, 1);
  demo.visibility(true);
  assert.equal(demo.intervals.size, 0);
  assert.equal(demo.node('#play-stack').textContent, '▷ Play');
});


test('training shows image and binary-mask pairs while review shows a colored overlay', () => {
  const demo = explorer();
  demo.stepButtons[1].click();
  assert.equal(demo.node('#workflow-art img').src, 'assets/neurite-mask.webp');
  assert.match(demo.node('#workflow-copy').textContent, /refine the segmentation/);
  demo.stepButtons[2].click();
  assert.equal(demo.node('#workflow-art img').src, 'assets/training-pairs.png');
  assert.match(demo.node('#workflow-copy').textContent, /confirmed binary mask/);
  assert.equal(demo.node('#workflow-art .art-caption').textContent, 'TRAINING SCHEMATIC / IMAGE + BINARY MASK PAIRS');
});
