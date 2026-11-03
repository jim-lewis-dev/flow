'use strict';

// The map displays existing flows. Only account positions are written back.
window.FlowExperimental = class {
  constructor({api, getState, getFlows, accept, notice, dateControl}) {
    this.api = api;
    this.getState = getState;
    this.getFlows = getFlows;
    this.notice = notice;
    this.dateControl = dateControl;
    this.accept = accept;
    this.pendingSaves = 0;
    this.reloadNeeded = false;
    this.$ = id => document.getElementById(id);
    this.svg = this.$('map-canvas');
    this.active = false;
    this.generation = 0;
    this.detailGeneration = 0;
    this.data = null;
    this.key = '';
    this.dateEdited = false;
    this.index = -1;
    this.progress = 0;
    this.playing = false;
    this.loading = false;
    this.loadError = false;
    this.speed = 1;
    this.camera = {x:0, y:0, scale:1};
    this.pointers = new Map();
    this.gesture = null;
    this.paths = new Map();
    this.frame = null;
    this.bind();
  }

  bind() {
    const on = (id, event, action) => this.$(id).addEventListener(event, action);
    on('map-units', 'change', () => this.load(true));
    on('map-basis', 'change', () => this.load(true));
    for (const id of ['map-start', 'map-end']) this.dateControl(id, () => {
      this.dateEdited = !!(this.$('map-start').value || this.$('map-end').value);
      return this.load(true);
    }, message => this.message(message, true));
    on('map-fit', 'click', () => { this.fit(); this.draw(); });
    on('map-first', 'click', () => this.seek(-1));
    on('map-previous', 'click', () => this.seek(Math.max(-1, this.index - 1)));
    on('map-next', 'click', () => this.seek(Math.min(this.groups().length - 1, this.index + 1)));
    on('map-last', 'click', () => this.seek(this.groups().length - 1));
    on('map-play', 'click', () => this.playing ? this.pause() : this.play());
    on('map-speed', 'input', () => {
      this.speed = Number(this.$('map-speed').value);
      this.$('map-speed-label').textContent = this.speed + '×';
      if (!this.speed) this.pause();
      this.controls();
    });
    on('map-detail-close', 'click', () => this.$('map-detail').close());
    this.svg.addEventListener('pointerdown', event => this.pointerDown(event));
    this.svg.addEventListener('pointermove', event => this.pointerMove(event));
    this.svg.addEventListener('pointerup', event => this.pointerUp(event));
    this.svg.addEventListener('pointercancel', event => this.pointerUp(event, true));
    this.svg.addEventListener('lostpointercapture', event => {
      if (this.pointers.has(event.pointerId)) this.pointerUp(event, true);
    });
    this.svg.addEventListener('wheel', event => {
      event.preventDefault();
      const p = this.point(event);
      this.zoomAt(p, this.camera.scale * Math.exp(-event.deltaY * .002));
      this.draw();
    }, {passive:false});
    this.svg.addEventListener('keydown', event => {
      if (event.key === ' ' && event.target === this.svg) {
        event.preventDefault();
        this.playing ? this.pause() : this.play();
      }
    });
    this.resizeObserver = new ResizeObserver(() => {
      if (!this.active) return;
      const size = this.size();
      if (this.lastSize) {
        this.camera.x += (size.width - this.lastSize.width) / 2;
        this.camera.y += (size.height - this.lastSize.height) / 2;
      }
      this.lastSize = size;
      this.draw();
    });
    this.resizeObserver.observe(this.svg);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.pause();
    });
  }

  groups() { return this.data?.groups || []; }
  size() { return {width:this.svg.clientWidth || 320, height:this.svg.clientHeight || 380}; }
  point(event) {
    const rect = this.svg.getBoundingClientRect();
    return {x:event.clientX - rect.left, y:event.clientY - rect.top};
  }
  world(point) {
    return {x:(point.x - this.camera.x) / this.camera.scale,
            y:(point.y - this.camera.y) / this.camera.scale};
  }
  project(node) {
    return {x:node.x * 1000 * this.camera.scale + this.camera.x,
            y:node.y * 510 * this.camera.scale + this.camera.y};
  }
  svgNode(tag, attrs = {}, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = text;
    return node;
  }

  reset() {
    this.pause();
    this.generation++;
    this.detailGeneration++;
    this.data = null;
    this.key = '';
    this.loading = false;
    this.loadError = false;
    this.dateEdited = false;
    this.index = -1;
    this.progress = 0;
    this.$('map-start').value = this.$('map-end').value = '';
    this.$('map-units').replaceChildren();
    this.$('map-basis').value = 'Confirmed';
    if (this.$('map-detail').open) this.$('map-detail').close();
    this.draw();
  }

  setActive(active) {
    this.active = active;
    if (active) this.load();
    else this.pause();
  }

  changed() {
    if (this.active) this.load();
  }

  args() {
    const args = {
      flow_ids:this.getFlows().map(flow => flow.flow_id),
      units:this.getFlows().some(flow => flow.units === this.$('map-units').value) ? this.$('map-units').value : '',
      basis:this.$('map-basis').value
    };
    if (this.dateEdited) {
      if (this.$('map-start').value) args.start = this.$('map-start').value;
      if (this.$('map-end').value) args.end = this.$('map-end').value;
    }
    return args;
  }

  async load(force = false) {
    const state = this.getState();
    if (!this.active || !state) return;
    if (this.gesture || this.pendingSaves) { this.reloadNeeded = true; return; }
    this.reloadNeeded = false;
    const args = this.args();
    const key = JSON.stringify([state.dataset, this.getFlows(), state.accounts, state.map_positions, args]);
    if (!force && key === this.key) { this.draw(); return; }
    const generation = ++this.generation;
    const oldDate = this.groups()[this.index]?.date;
    this.loading = true;
    this.pause();
    this.message('Loading map…');
    try {
      const data = await this.api('experimental', args);
      if (generation !== this.generation || state.dataset !== this.getState()?.dataset) return;
      if (this.gesture || this.pendingSaves) { this.reloadNeeded = true; return; }
      const first = !this.data || this.data.units !== data.units;
      this.key = key;
      this.loadError = false;
      this.data = data;
      this.requestArgs = {...args, units:data.units};
      this.$('map-units').replaceChildren(...data.available_units.map(value => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        return option;
      }));
      this.$('map-units').value = data.units;
      if (!this.dateEdited) {
        this.$('map-start').value = (data.start || '').slice(0, 10);
        this.$('map-end').value = (data.end || '').slice(0, 10);
      }
      this.index = force ? -1 : this.groups().findIndex(group => group.date === oldDate);
      this.progress = this.index < 0 ? 0 : 1;
      if (first) this.fit();
      this.message(this.groups().length ? '' : 'No flows match this unit, period, and status basis.');
      this.draw();
    } catch (error) {
      if (generation === this.generation) { this.loadError = true; this.message(error.message, true); }
    } finally {
      if (generation === this.generation) { this.loading = false; this.controls(); }
    }
  }

  message(text, error = false) {
    this.$('map-message').textContent = text;
    this.$('map-message').hidden = !text;
    this.$('map-message').classList.toggle('error', error);
  }

  fit() {
    const nodes = this.data?.nodes || [];
    const {width, height} = this.size();
    if (!nodes.length) return;
    const xs = nodes.map(node => node.x * 1000), ys = nodes.map(node => node.y * 510);
    const loX = Math.min(...xs), hiX = Math.max(...xs), loY = Math.min(...ys), hiY = Math.max(...ys);
    const scale = Math.min(1.5, Math.max(.02, Math.min((width - 136) / Math.max(100, hiX - loX),
                                                  (height - 100) / Math.max(100, hiY - loY))));
    this.camera = {scale, x:width / 2 - (loX + hiX) / 2 * scale,
                          y:height / 2 - (loY + hiY) / 2 * scale};
    this.lastSize = {width, height};
  }

  zoomAt(point, scale) {
    const before = this.world(point);
    this.camera.scale = Math.max(.01, Math.min(6, scale));
    this.camera.x = point.x - before.x * this.camera.scale;
    this.camera.y = point.y - before.y * this.camera.scale;
  }

  seek(index) {
    this.pause();
    this.index = index;
    this.progress = index < 0 ? 0 : 1;
    this.drawPulses();
    this.controls();
  }

  play() {
    if (!this.groups().length || !this.speed || this.playing || this.loading || this.loadError) return;
    if (this.index >= this.groups().length - 1 && this.progress >= 1) {
      this.index = -1;
      this.progress = 0;
    }
    if (this.index < 0) this.index = 0;
    else if (this.progress >= 1) { this.index++; this.progress = 0; }
    this.playing = true;
    this.lastTick = performance.now();
    this.controls();
    this.frame = requestAnimationFrame(time => this.tick(time));
  }

  pause() {
    this.playing = false;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.controls();
  }

  tick(time) {
    if (!this.playing || !this.active) return;
    this.progress += Math.min(.1, (time - this.lastTick) / 1000) * this.speed;
    this.lastTick = time;
    if (this.progress >= 1.18) {
      this.progress = 0;
      this.index++;
      if (this.index >= this.groups().length) {
        this.index = this.groups().length - 1;
        this.progress = 1;
        this.pause();
      }
      this.controls();
    }
    this.drawPulses();
    if (this.playing) this.frame = requestAnimationFrame(next => this.tick(next));
  }

  controls() {
    const count = this.groups().length, blocked = this.loading || this.loadError;
    const end = this.index >= count - 1 && this.progress >= 1;
    this.$('map-first').disabled = blocked || !count || this.index < 0;
    this.$('map-previous').disabled = blocked || !count || this.index < 0;
    this.$('map-next').disabled = blocked || !count || end;
    this.$('map-last').disabled = blocked || !count || end;
    this.$('map-play').disabled = blocked || !count || !this.speed;
    this.$('map-play').textContent = this.playing ? '❚❚' : '▶';
    this.$('map-play').setAttribute('aria-label', this.playing ? 'Pause' : 'Play');
    this.$('map-play').title = this.playing ? 'Pause' : 'Play';
    const group = this.groups()[this.index];
    this.$('map-time').textContent = group ? group.date : count ? 'Start of period' : 'No flows';
    this.$('map-step').textContent = count ? `${Math.max(0, this.index + 1)} / ${count}` : '';
  }

  geometry(source, target, bidirectional) {
    const a = this.project(source), b = this.project(target);
    if (source.name === target.name) {
      const r = Math.max(32, Math.min(80, this.camera.scale * 95));
      const p1 = {x:a.x - r, y:a.y - r * 1.8}, p2 = {x:a.x + r, y:a.y - r * 1.8};
      return {d:`M${a.x - 15},${a.y - 12} C${p1.x},${p1.y} ${p2.x},${p2.y} ${a.x + 15},${a.y - 12}`,
        point:t => {
          const s = 1 - t;
          return {x:s*s*s*(a.x-15)+3*s*s*t*p1.x+3*s*t*t*p2.x+t*t*t*(a.x+15),
                  y:s*s*s*(a.y-12)+3*s*s*t*p1.y+3*s*t*t*p2.y+t*t*t*(a.y-12)};
        }};
    }
    const dx = b.x - a.x, dy = b.y - a.y, length = Math.max(1, Math.hypot(dx, dy));
    const offset = bidirectional ? Math.min(35, length * .2) : 0;
    const middle = {x:(a.x + b.x) / 2 - dy / length * offset,
                    y:(a.y + b.y) / 2 + dx / length * offset};
    return {d:`M${a.x},${a.y} Q${middle.x},${middle.y} ${b.x},${b.y}`,
      point:t => ({x:(1-t)*(1-t)*a.x+2*(1-t)*t*middle.x+t*t*b.x,
                   y:(1-t)*(1-t)*a.y+2*(1-t)*t*middle.y+t*t*b.y})};
  }

  draw() {
    const {width, height} = this.size();
    this.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    this.svg.replaceChildren();
    this.paths.clear();
    this.pulseLayer = this.svgNode('g', {'aria-label':'Flows at current time'});
    const nodes = this.data?.nodes || [], lookup = new Map(nodes.map(node => [node.name, node]));
    const pairs = new Map();
    for (const group of this.groups()) for (const flow of group.flows) {
      const key = JSON.stringify([flow.source, flow.target]);
      pairs.set(key, [flow.source, flow.target]);
    }
    const edges = this.svgNode('g', {class:'map-edges'});
    for (const [key, [from, to]] of pairs) {
      if (!lookup.has(from) || !lookup.has(to)) continue;
      const shape = this.geometry(lookup.get(from), lookup.get(to), pairs.has(JSON.stringify([to, from])));
      this.paths.set(key, shape);
      edges.append(this.svgNode('path', {d:shape.d, fill:'none', stroke:'#b6cabe', 'stroke-width':1.5}));
      if (from !== to) {
        const tip = shape.point(.68), before = shape.point(.66);
        const angle = Math.atan2(tip.y - before.y, tip.x - before.x) * 180 / Math.PI;
        edges.append(this.svgNode('path', {d:'M-5,-3 L0,0 L-5,3', fill:'none', stroke:'#98b1a3',
          'stroke-width':1.5, transform:`translate(${tip.x},${tip.y}) rotate(${angle})`}));
      }
    }
    this.svg.append(edges);
    const nodeLayer = this.svgNode('g', {'aria-label':'Accounts'});
    this.nodeHits = [];
    const density = Math.max(0, Math.min(1, (this.camera.scale - .3) / .4));
    const fontSize = 9 + 2 * density, nodeHeight = 28 + 14 * density;
    for (const node of nodes) {
      const p = this.project(node), width = Math.min(124, Math.max(46, Math.min(17,node.name.length) * fontSize * .56 + 18));
      this.nodeHits.push({name:node.name, x:p.x, y:p.y, width});
      const group = this.svgNode('g', {class:'map-node', transform:`translate(${p.x},${p.y})`,
        'data-account':node.name, role:'button', tabindex:'0', 'aria-label':`${node.name}, account details`});
      group.append(this.svgNode('rect', {x:-Math.max(44,width)/2, y:-22, width:Math.max(44,width), height:44, fill:'transparent'}));
      group.append(this.svgNode('rect', {x:-width/2, y:-nodeHeight/2, width, height:nodeHeight, rx:Math.min(15,nodeHeight/2),
        fill:node.opening_amount === null ? '#f9faf6' : '#e2ecdc', stroke:'#82a58f', 'stroke-width':1.3}));
      group.append(this.svgNode('text', {x:0, y:fontSize*.35, 'text-anchor':'middle', fill:'#234c3c', 'font-size':fontSize,
        'font-weight':600, 'pointer-events':'none'}, node.name.length > 17 ? node.name.slice(0,16)+'…' : node.name));
      group.append(this.svgNode('title', {}, node.name));
      group.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.showNode(node.name); }
      });
      nodeLayer.append(group);
    }
    this.svg.append(nodeLayer, this.pulseLayer);
    this.drawPulses();
    this.controls();
  }

  drawPulses() {
    if (!this.pulseLayer) return;
    this.pulseLayer.replaceChildren();
    this.pulseHits = [];
    const group = this.groups()[this.index];
    if (!group) return;
    const colors = {Complete:'#277d63', Pending:'#c18731', Planned:'#6b7db6'};
    const counts = new Map(), slots = new Map();
    for (const flow of group.flows) {
      const key = JSON.stringify([flow.source, flow.target]);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    for (const flow of group.flows) {
      const key = JSON.stringify([flow.source, flow.target]);
      const path = this.paths.get(key);
      if (!path) continue;
      // Leave paused pulses beside their target so they remain tappable.
      const t = .12 + .70 * Math.max(0, Math.min(1, this.progress));
      const point = path.point(t), next = path.point(Math.min(1, t + .01));
      const slot = slots.get(key) || 0, offset = (slot - (counts.get(key) - 1) / 2) * 18;
      slots.set(key, slot + 1);
      const dx = next.x - point.x, dy = next.y - point.y, length = Math.max(.01, Math.hypot(dx,dy));
      point.x -= dy / length * offset;
      point.y += dx / length * offset;
      this.pulseHits.push({id:flow.flow_id, x:point.x, y:point.y});
      const pulse = this.svgNode('g', {'data-flow':flow.flow_id, role:'button', tabindex:'0',
        'aria-label':`${flow.name || 'Flow'}: ${flow.amount_display} ${flow.units}`, class:'map-pulse'});
      pulse.append(this.svgNode('circle', {cx:point.x, cy:point.y, r:Math.max(20, flow.radius), fill:'transparent'}));
      pulse.append(this.svgNode('circle', {cx:point.x, cy:point.y, r:Math.min(14, Math.max(5, flow.radius)),
        fill:colors[flow.status] || colors.Complete, stroke:'#fff', 'stroke-width':2, 'pointer-events':'none'}));
      pulse.append(this.svgNode('title', {}, `${flow.amount_display} ${flow.units} · ${flow.name}`));
      pulse.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.showFlow(flow); }
      });
      this.pulseLayer.append(pulse);
    }
  }

  pointerDown(event) {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    event.preventDefault();
    const point = this.point(event);
    this.svg.setPointerCapture(event.pointerId);
    this.pointers.set(event.pointerId, point);
    if (this.pointers.size === 2) {
      if (this.gesture?.node && this.gesture.moved) this.saveNode(this.gesture.node, this.gesture.storedPosition);
      const [a, b] = [...this.pointers.values()], center = {x:(a.x+b.x)/2, y:(a.y+b.y)/2};
      this.gesture = {pinch:true, distance:Math.max(10, Math.hypot(b.x-a.x,b.y-a.y)),
        scale:this.camera.scale, anchor:this.world(center), moved:true};
      return;
    }
    if (this.pointers.size > 2) return;
    let account = event.target.closest('[data-account]')?.getAttribute('data-account');
    let flowId = event.target.closest('[data-flow]')?.getAttribute('data-flow');
    if (flowId || !account) {
      // Enlarged touch targets may overlap; choose the nearest visible dot.
      const nearest = (this.pulseHits || []).map(hit => ({...hit, distance:Math.hypot(hit.x-point.x,hit.y-point.y)}))
        .filter(hit => hit.distance <= 22).sort((a,b) => a.distance-b.distance)[0];
      if (nearest) flowId = nearest.id;
    }
    if (account && !flowId) {
      const nearest = (this.nodeHits || []).filter(hit => Math.abs(hit.x-point.x) <= Math.max(22,hit.width/2) && Math.abs(hit.y-point.y) <= 22)
        .sort((a,b) => Math.hypot(a.x-point.x,a.y-point.y)-Math.hypot(b.x-point.x,b.y-point.y))[0];
      if (nearest) account = nearest.name;
    }
    const node = this.data?.nodes.find(item => item.name === account);
    if (node || flowId) this.pause();
    this.gesture = {start:point, previous:point, node, flowId, moved:false,
      original:node ? {x:node.x, y:node.y} : null,
      storedPosition:node ? (this.getState()?.map_positions?.[node.name] ?? null) : null, camera:{...this.camera}};
  }

  pointerMove(event) {
    if (!this.pointers.has(event.pointerId) || !this.gesture) return;
    event.preventDefault();
    const point = this.point(event);
    this.pointers.set(event.pointerId, point);
    const g = this.gesture;
    if (g.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()], center = {x:(a.x+b.x)/2, y:(a.y+b.y)/2};
      const distance = Math.max(10, Math.hypot(b.x-a.x,b.y-a.y));
      this.camera.scale = Math.max(.01, Math.min(6, g.scale * distance / g.distance));
      this.camera.x = center.x - g.anchor.x * this.camera.scale;
      this.camera.y = center.y - g.anchor.y * this.camera.scale;
    } else if (!g.pinch) {
      const dx = point.x - g.start.x, dy = point.y - g.start.y;
      if (Math.hypot(dx,dy) > 5) g.moved = true;
      if (!g.moved) return;
      if (g.node) {
        g.node.x = g.original.x + dx / this.camera.scale / 1000;
        g.node.y = g.original.y + dy / this.camera.scale / 510;
      } else {
        this.camera.x = g.camera.x + dx;
        this.camera.y = g.camera.y + dy;
      }
    }
    this.draw();
  }

  pointerUp(event, canceled = false) {
    const g = this.gesture;
    if (!g || !this.pointers.has(event.pointerId)) return;
    this.pointers.delete(event.pointerId);
    if (this.svg.hasPointerCapture(event.pointerId)) this.svg.releasePointerCapture(event.pointerId);
    if (g.pinch) {
      if (this.pointers.size) {
        const point = [...this.pointers.values()][0];
        this.gesture = {start:point, previous:point, moved:true, camera:{...this.camera}};
      } else { this.gesture = null; if (this.reloadNeeded) this.load(); }
      return;
    }
    this.gesture = null;
    if (g.node && g.moved) this.saveNode(g.node, g.storedPosition);
    else if (!canceled && !g.moved) {
      if (g.node) this.showNode(g.node.name);
      else if (g.flowId) {
        const flow = this.groups()[this.index]?.flows.find(item => item.flow_id === g.flowId);
        if (flow) this.showFlow(flow);
      }
    }
    if (this.reloadNeeded && !this.pendingSaves) this.load();
  }

  async saveNode(node, baseline = null) {
    const dataset = this.getState()?.dataset;
    this.pendingSaves++;
    try {
      const result = await this.api('save_positions', {dataset, positions:{[node.name]:[node.x,node.y]}, baseline:{[node.name]:baseline}});
      if (dataset === this.getState()?.dataset) this.accept(result);
    } catch (error) { this.notice('Could not save map position: '+error.message, true); }
    finally {
      this.pendingSaves--;
      this.reloadNeeded = true;
      if (!this.gesture && !this.pendingSaves) this.load(true);
    }
  }

  detail(title, pairs, note = '') {
    this.$('map-detail-title').textContent = title;
    const body = this.$('map-detail-body');
    body.replaceChildren();
    const list = document.createElement('dl');
    list.className = 'map-detail-list';
    for (const [label, value] of pairs) {
      const term = document.createElement('dt'), detail = document.createElement('dd');
      term.textContent = label;
      detail.textContent = value;
      list.append(term, detail);
    }
    body.append(list);
    if (note) { const text=document.createElement('p');text.className='small muted';text.textContent=note;body.append(text); }
    if (!this.$('map-detail').open) this.$('map-detail').showModal();
  }

  async showNode(name) {
    this.pause();
    const generation = ++this.detailGeneration, dataset = this.getState()?.dataset;
    this.detail(name, [], 'Loading account details…');
    try {
      const result = await this.api('experimental_details', {...this.requestArgs, name, step:this.index});
      if (generation !== this.detailGeneration || dataset !== this.getState()?.dataset || !this.$('map-detail').open) return;
      const qty = value => `${value} ${result.units}`;
      const pairs = [[result.at ? 'At' : 'Period', result.at || `${this.data.start} – ${this.data.end}`],
        ['Received', qty(result.incoming_display)], ['Sent', qty(result.outgoing_display)],
        ['Starting balance', result.opening_amount === null ? 'Unknown' : qty(result.opening_display)]];
      if (result.at) pairs.splice(1, 0, ['Balance', result.balance === null ? 'Unknown' : qty(result.balance_display)]);
      if (result.opening_at) pairs.push(['Starting date', result.opening_at]);
      this.detail(name, pairs, `${result.scope}. Balances use all eligible flows; sent and received use this map.`);
    } catch (error) {
      if (generation === this.detailGeneration) this.detail(name, [], error.message);
    }
  }

  showFlow(flow) {
    this.pause();
    this.detailGeneration++;
    const pairs = [['Date',flow.date], ['From',flow.from_account], ['To',flow.to_account],
      ['Amount',`${flow.amount_display} ${flow.units}`], ['Status',flow.status], ['Category',flow.category]];
    if (flow.tags?.length) pairs.push(['Tags',flow.tags.join(', ')]);
    if (flow.notes) pairs.push(['Notes',flow.notes]);
    this.detail(flow.name || 'Flow', pairs, flow.source !== flow.from_account ? 'Negative amount: the resource moves in the opposite direction.' : '');
  }
};
