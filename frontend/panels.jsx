import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { NativeSelect } from "./ui/native-select";
import { toast } from "./notifications";

const emptyBoxes = [];
const zoomLevels = [40, 55, 70, 85, 100, 125];
const selectClass = "h-9 min-w-0 rounded-md border border-input bg-background pl-3 text-sm";
const handles = [
  ["nw", "-left-1.5 -top-1.5 cursor-nwse-resize"],
  ["n", "-top-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize"],
  ["ne", "-right-1.5 -top-1.5 cursor-nesw-resize"],
  ["e", "-right-1.5 top-1/2 -translate-y-1/2 cursor-ew-resize"],
  ["se", "-bottom-1.5 -right-1.5 cursor-nwse-resize"],
  ["s", "-bottom-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize"],
  ["sw", "-bottom-1.5 -left-1.5 cursor-nesw-resize"],
  ["w", "-left-1.5 top-1/2 -translate-y-1/2 cursor-ew-resize"],
];
const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function adjust(box, dx, dy, edge, width, height) {
  const [left, top, right, bottom] = box;
  if (edge === "move") {
    dx = clamp(dx, -left, width - right);
    dy = clamp(dy, -top, height - bottom);
    return [left + dx, top + dy, right + dx, bottom + dy];
  }
  return [
    edge.includes("w") ? clamp(left + dx, 0, right - 1) : left,
    edge.includes("n") ? clamp(top + dy, 0, bottom - 1) : top,
    edge.includes("e") ? clamp(right + dx, left + 1, width) : right,
    edge.includes("s") ? clamp(bottom + dy, top + 1, height) : bottom,
  ];
}

function readingOrder(a, b) {
  return a.box[1] - b.box[1] || a.box[0] - b.box[0];
}

async function request(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(typeof result.detail === "string" ? result.detail : "Bliss could not finish that action.");
  }
  return response;
}

function send(path, payload, method = "POST") {
  return request(path, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

export function Panels({ active }) {
  const [seriesList, setSeriesList] = useState(null);
  const [series, setSeries] = useState("");
  const [chapter, setChapter] = useState("");
  const [document, setDocument] = useState(null);
  const [boxes, setBoxes] = useState(emptyBoxes);
  const [saved, setSaved] = useState(emptyBoxes);
  const [undo, setUndo] = useState([]);
  const [selected, setSelected] = useState(null);
  const [pending, setPending] = useState("");
  const [progress, setProgress] = useState(0);
  const [zoomIndex, setZoomIndex] = useState(0);
  const [draft, setDraft] = useState(null);
  const [tileError, setTileError] = useState(false);
  const drag = useRef(null);
  const nextBoxId = useRef(0);
  const canvas = useRef(null);
  const viewport = useRef(null);
  const zoomAnchor = useRef(null);
  const zoom = zoomLevels[zoomIndex];
  const dirty = boxes !== saved;
  const query = new URLSearchParams({ series, chapter });
  const canEdit = document?.boxes != null;

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    request("/api/panels/series", { signal: controller.signal })
      .then(response => response.json())
      .then(setSeriesList)
      .catch(error => { if (!controller.signal.aborted) toast.error(error.message); });
    return () => controller.abort();
  }, [active]);

  useEffect(() => {
    if (!chapter) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ series, chapter });
    request(`/api/panels/chapter?${params}`, { signal: controller.signal })
      .then(response => response.json())
      .then(result => { if (!controller.signal.aborted) openReview(result); })
      .catch(error => { if (!controller.signal.aborted) toast.error(error.message); });
    return () => controller.abort();
  }, [series, chapter]);

  useEffect(() => {
    if (!dirty) return;
    function confirmClose(event) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", confirmClose);
    return () => window.removeEventListener("beforeunload", confirmClose);
  }, [dirty]);

  useLayoutEffect(() => {
    const anchor = zoomAnchor.current;
    zoomAnchor.current = null;
    if (!anchor || !canvas.current) return;
    const scroller = viewport.current;
    const view = scroller.getBoundingClientRect();
    const strip = canvas.current.getBoundingClientRect();
    scroller.scrollLeft += strip.left + anchor.x * strip.width - view.left - scroller.clientWidth / 2;
    scroller.scrollTop += strip.top + anchor.y * strip.height - view.top - anchor.offsetY;
  }, [zoom]);

  function changeZoom(direction) {
    const next = zoomIndex + direction;
    if (drag.current || next < 0 || next >= zoomLevels.length) return;
    const scroller = viewport.current;
    const view = scroller.getBoundingClientRect();
    const strip = canvas.current.getBoundingClientRect();
    const offsetY = scroller.scrollTop === 0 ? 0 : scroller.clientHeight / 2;
    zoomAnchor.current = {
      x: clamp((view.left + scroller.clientWidth / 2 - strip.left) / strip.width, 0, 1),
      y: clamp((view.top + offsetY - strip.top) / strip.height, 0, 1),
      offsetY,
    };
    setZoomIndex(next);
  }

  function openReview(result) {
    const records = (result?.boxes ?? []).map((box, index) => ({ id: index, box }));
    nextBoxId.current = records.length;
    setDocument(result);
    setBoxes(records);
    setSaved(records);
    setUndo([]);
    setSelected(null);
    setTileError(false);
  }

  function selectChapter(nextSeries, nextChapter) {
    if (dirty && !window.confirm("Discard unsaved panel edits?")) return;
    openReview(null);
    setSeries(nextSeries);
    setChapter(nextChapter);
    viewport.current?.scrollTo(0, 0);
  }

  async function detect() {
    const progressId = crypto.randomUUID();
    const controller = new AbortController();
    let timer;
    setPending("detect");
    setProgress(0);
    async function poll() {
      try {
        const response = await request(`/api/panels/progress/${progressId}`, { signal: controller.signal });
        const { percent } = await response.json();
        if (!controller.signal.aborted && percent !== null) setProgress(percent);
      } catch {
        // The detection request reports failures.
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 750);
      }
    }
    timer = setTimeout(poll, 400);
    try {
      const response = await send("/api/panels/detect", { series, chapter, source: document.source, progress_id: progressId });
      openReview(await response.json());
    } catch (error) {
      toast.error("Detection failed", { description: error.message });
    } finally {
      controller.abort();
      clearTimeout(timer);
      setPending("");
    }
  }

  async function save() {
    const snapshot = boxes;
    setPending("save");
    try {
      const payload = { series, chapter, source: document.source, boxes: snapshot.map(row => row.box) };
      await send("/api/panels/chapter", payload, "PUT");
      setSaved(snapshot);
      toast.success("Panel edits saved");
    } catch (error) {
      toast.error(error.message);
    } finally {
      setPending("");
    }
  }

  function apply(next) {
    setUndo(history => [...history.slice(-49), boxes]);
    setBoxes(next.sort(readingOrder));
  }

  function undoEdit() {
    if (!undo.length) return;
    setBoxes(undo.at(-1));
    setUndo(undo.slice(0, -1));
    setSelected(null);
  }

  function remove() {
    if (selected === null) return;
    apply(boxes.filter(row => row.id !== selected));
    setSelected(null);
    viewport.current.focus({ preventScroll: true });
  }

  function replaceBox(id, box) {
    apply(boxes.map(row => row.id === id ? { id, box } : row));
  }

  function begin(event, row, edge) {
    if (event.button !== 0 || pending || drag.current) return;
    event.stopPropagation();
    setSelected(row?.id ?? null);
    const rect = canvas.current.getBoundingClientRect();
    drag.current = {
      id: row?.id ?? null, original: row?.box, box: row?.box, edge,
      pointerId: event.pointerId,
      x: event.clientX - rect.left, y: event.clientY - rect.top, moved: false, frame: 0,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function beginDrawing(event) {
    if (!canEdit || event.button !== 0 || pending || drag.current) return;
    if (!event.shiftKey && event.target.closest("button")) return;
    event.preventDefault();
    viewport.current.focus({ preventScroll: true });
    begin(event, null, "draw");
  }

  function boxStyle([left, top, right, bottom]) {
    return {
      left: `${left / document.width * 100}%`, top: `${top / document.height * 100}%`,
      width: `${(right - left) / document.width * 100}%`, height: `${(bottom - top) / document.height * 100}%`,
    };
  }

  function updateDrag(edit, clientX, clientY) {
    const rect = canvas.current.getBoundingClientRect();
    const dx = clientX - rect.left - edit.x;
    const dy = clientY - rect.top - edit.y;
    if (!edit.moved && Math.hypot(dx, dy) < 3) return;
    edit.moved = true;
    const scale = document.width / rect.width;
    if (edit.edge === "draw") {
      const x1 = clamp(Math.round(edit.x * scale), 0, document.width);
      const y1 = clamp(Math.round(edit.y * scale), 0, document.height);
      const x2 = clamp(Math.round((edit.x + dx) * scale), 0, document.width);
      const y2 = clamp(Math.round((edit.y + dy) * scale), 0, document.height);
      edit.box = [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)];
    } else {
      edit.box = adjust(edit.original, Math.round(dx * scale), Math.round(dy * scale),
        edit.edge, document.width, document.height);
    }
    setDraft({ id: edit.id, box: edit.box });
  }

  function move(event) {
    const edit = drag.current;
    if (!edit || edit.pointerId !== event.pointerId) return;
    edit.clientX = event.clientX;
    edit.clientY = event.clientY;
    updateDrag(edit, edit.clientX, edit.clientY);
    if (!edit.moved || edit.frame) return;

    function scrollAtEdge() {
      edit.frame = 0;
      const scroller = viewport.current;
      const rect = scroller.getBoundingClientRect();
      const margin = 36;
      const delta = edit.clientY < rect.top + margin ? edit.clientY - rect.top - margin
        : Math.max(0, edit.clientY - rect.bottom + margin);
      if (!delta) return;
      const before = scroller.scrollTop;
      scroller.scrollTop += clamp(Math.round(delta / 3), -20, 20);
      if (scroller.scrollTop === before) return;
      updateDrag(edit, edit.clientX, edit.clientY);
      edit.frame = requestAnimationFrame(scrollAtEdge);
    }

    edit.frame = requestAnimationFrame(scrollAtEdge);
  }

  function end(event, cancelled = false) {
    const edit = drag.current;
    if (!edit || edit.pointerId !== event.pointerId) return;
    cancelAnimationFrame(edit.frame);
    if (!cancelled) updateDrag(edit, event.clientX, event.clientY);
    drag.current = null;
    setDraft(null);
    if (cancelled || !edit.moved) return;
    if (edit.edge === "draw") {
      const [left, top, right, bottom] = edit.box;
      if (left === right || top === bottom) return;
      const id = nextBoxId.current++;
      apply([...boxes, { id, box: edit.box }]);
      setSelected(id);
    } else if (edit.box.some((value, i) => value !== edit.original[i])) {
      replaceBox(edit.id, edit.box);
    }
  }

  function keyboard(event) {
    if (pending || drag.current) return;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z" && !event.shiftKey) {
      event.preventDefault();
      undoEdit();
      return;
    }
    if (event.key === "Escape") {
      setSelected(null);
      viewport.current.focus({ preventScroll: true });
      return;
    }
    const row = boxes.find(item => item.id === selected);
    if (!row) return;
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      remove();
    }
    const direction = arrows[event.key];
    if (direction && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      const step = event.shiftKey ? 10 : 1;
      const edge = event.target.dataset.edge || "move";
      const box = adjust(row.box, direction[0] * step, direction[1] * step, edge, document.width, document.height);
      if (box.some((value, index) => value !== row.box[index])) replaceBox(row.id, box);
    }
  }

  const chapters = seriesList?.find(item => item.name === series)?.chapters ?? [];
  return (
    <section className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid min-w-0 max-w-64 gap-1">
          <Label htmlFor="panel-series">Series</Label>
          <NativeSelect id="panel-series" className={selectClass} value={series} disabled={!!pending}
            onChange={event => selectChapter(event.target.value, "")}>
            <option value="">Choose series</option>
            {seriesList?.map(item => <option key={item.name}>{item.name}</option>)}
          </NativeSelect>
        </div>
        <div className="grid min-w-0 max-w-64 gap-1">
          <Label htmlFor="panel-chapter">Chapter</Label>
          <NativeSelect id="panel-chapter" className={selectClass} value={chapter} disabled={!series || !!pending}
            onChange={event => selectChapter(series, event.target.value)}>
            <option value="">Choose chapter</option>
            {chapters.map(name => <option key={name}>{name}</option>)}
          </NativeSelect>
        </div>
        {document && !canEdit && <Button aria-live="polite" disabled={!!pending || tileError} onClick={detect}>
          {pending === "detect" ? `Detecting… ${progress}%` : "Detect Panels"}
        </Button>}
        {document && (
          <div role="group" aria-label="Strip zoom" className="flex items-center gap-1">
            <Button type="button" variant="outline" size="icon" aria-label="Zoom out" title="Zoom out"
              disabled={zoomIndex === 0} onClick={() => changeZoom(-1)}>−</Button>
            <span aria-live="polite" className="w-11 text-center text-xs tabular-nums text-muted-foreground">{zoom}%</span>
            <Button type="button" variant="outline" size="icon" aria-label="Zoom in" title="Zoom in"
              disabled={zoomIndex === zoomLevels.length - 1} onClick={() => changeZoom(1)}>+</Button>
          </div>
        )}
      </div>
      {seriesList?.length === 0 && <p className="text-sm text-muted-foreground">Stitch a chapter to review its panels here.</p>}
      {canEdit && (
        <div className="flex flex-wrap items-center gap-2" onKeyDown={keyboard}>
          <Button variant="outline" disabled={selected === null || !!pending} onClick={remove}>Delete</Button>
          <Button variant="outline" disabled={!undo.length || !!pending} onClick={undoEdit}>Undo</Button>
          <Button variant="outline" disabled={!dirty || !!pending} onClick={() => save()}>Save Edits</Button>
          <span role="status" className="text-xs text-muted-foreground">{boxes.length} {boxes.length === 1 ? "panel" : "panels"}</span>
          <p className="basis-full text-xs text-muted-foreground">Drag outside a box to draw. Drag a box to move it, or drag a handle to resize. Shift-drag to draw over a box.</p>
        </div>
      )}
      <div ref={viewport} tabIndex={document ? 0 : undefined} onKeyDown={keyboard}
        aria-label="Chapter artwork" className="min-h-0 flex-1 overflow-auto [scrollbar-width:none] rounded-xl focus-visible:outline focus-visible:outline-white/40">
        {document && (
          <div ref={canvas}
            className={`relative mx-auto select-none ${canEdit && !pending ? "cursor-crosshair" : ""}`}
            style={{ width: document.width * zoom / 100 }}
            onPointerDownCapture={beginDrawing} onPointerMove={move}
            onPointerUp={event => end(event)} onPointerCancel={event => end(event, true)}
            onLostPointerCapture={event => end(event, true)}>
            {Array.from({ length: Math.ceil(document.height / document.tile_height) }, (_, index) => (
              <img key={`${document.source}-${index}`}
                src={`/api/panels/tile/${index}?${query}&source=${document.source}`}
                width={document.width} height={Math.min(document.tile_height, document.height - index * document.tile_height)}
                className="block h-auto w-full bg-neutral-900" alt="" draggable={false} loading="lazy"
                onError={() => {
                  if (!tileError) toast.error("Could not load chapter artwork. Reopen the chapter to retry.");
                  setTileError(true);
                }} />
            ))}
            <div className="pointer-events-none absolute inset-0">
              {draft?.id === null && (
                <div aria-hidden="true" style={boxStyle(draft.box)}
                  className="absolute z-20 border-2 border-dashed border-yellow-300 shadow-[0_0_0_1px_black]" />
              )}
              {boxes.map((row, index) => {
                const bounds = draft?.id === row.id ? draft.box : row.box;
                const id = `P${String(index + 1).padStart(3, "0")}`;
                const chosen = row.id === selected;
                return (
                  <div key={row.id}
                    className={`absolute ${chosen ? "z-10" : ""}`}
                    style={boxStyle(bounds)}>
                    <button type="button" aria-label={`Panel ${id}`} aria-pressed={chosen} disabled={!!pending}
                      className={`pointer-events-auto absolute inset-0 touch-none cursor-move border-yellow-300 shadow-[0_0_0_1px_black,inset_0_0_0_1px_black] ${chosen ? "border-4" : "border-[3px]"}`}
                      onPointerDown={event => begin(event, row, "move")} onFocus={() => setSelected(row.id)}
                      onClick={event => { event.stopPropagation(); setSelected(row.id); }}>
                      <span className="pointer-events-none absolute -left-0.5 -top-0.5 rounded-br bg-black px-1.5 py-0.5 text-[11px] font-semibold text-white">{id}</span>
                    </button>
                    {chosen && handles.map(([edge, position]) => (
                      <button key={edge} type="button" aria-label={`Resize panel ${id}, ${edge}`} data-edge={edge} disabled={!!pending}
                        className={`pointer-events-auto absolute size-3 touch-none rounded-sm border border-black bg-yellow-300 ${position}`}
                        onPointerDown={event => begin(event, row, edge)} />
                    ))}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
