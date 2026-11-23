import { useEffect, useRef } from "react";
import { paintTileOrb, stillSeconds } from "./tileOrb";
import { useActivityPresentation } from "./useActivityPresentation";
import "./ActivityOrb.css";

export type ActivityState = "idle" | "active" | "waiting" | "offline" | "error";

/** The tile orb is decorative; adjacent observed text supplies its meaning. */
export function ActivityOrb({ state }: { state: ActivityState }) {
  const ref = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const time = useRef(0);
  const previous = useRef(state);
  const presentation = useActivityPresentation(ref);
  const paused = state !== "active" || !presentation.motion;
  // biome-ignore lint/correctness/useExhaustiveDependencies: a theme change moves `--or`, which draw() reads from the stylesheet, so the still frame must repaint.
  useEffect(() => {
    // A turn that starts from rest opens with the genesis; a pause for an approval or a question
    // resumes where the film stopped.
    if (state !== previous.current) {
      if (state === "active" && previous.current !== "waiting") time.current = 0;
      previous.current = state;
    }
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    // paintTileOrb draws one frame; this component owns the frame loop and its lifecycle.
    const draw = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const backingSize = Math.round(64 * dpr);
      if (canvas.width !== backingSize || canvas.height !== backingSize) {
        canvas.width = backingSize;
        canvas.height = backingSize;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, 64, 64);
      const seconds = paused ? stillSeconds(state) : time.current;
      // The tiles take the theme's accent ink, read live so a theme change repaints in the other.
      const ink = getComputedStyle(canvas).getPropertyValue("--or").trim();
      paintTileOrb(ctx, seconds, ink, dpr, state);
    };
    let disposed = false;
    let frame = 0;
    let last = performance.now();
    let next = last + 1000 / 60;
    const tick = (now: number) => {
      if (disposed) return;
      if (now + 0.1 >= next) {
        time.current += (now - last) / 1000;
        last = now;
        next = Math.max(next + 1000 / 60, now);
        draw();
      }
      frame = requestAnimationFrame(tick);
    };
    draw();
    if (!paused) frame = requestAnimationFrame(tick);
    const resize = () => {
      if (!disposed) draw();
    };
    window.addEventListener("resize", resize);
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
    };
  }, [paused, state, presentation.theme]);
  return (
    <div
      ref={ref}
      className="activity-orb"
      data-state={state}
      data-visible={presentation.visible}
      data-paused={paused}
      aria-hidden="true"
    >
      <canvas
        ref={canvasRef}
        className="activity-orb-canvas"
        data-renderer="viki-tiles"
        width="64"
        height="64"
      />
      <svg
        className="activity-orb-state"
        viewBox="-10 -10 20 20"
        width="16"
        height="16"
        aria-hidden="true"
        focusable="false"
      >
        <circle r="9" />
        {state === "active" ? <path d="M-4 2V-1M0 4V-4M4 1V-2" /> : null}
        {state === "waiting" ? <path d="M-2-3V3M2-3V3" /> : null}
        {state === "offline" ? <path d="M-3 3L3-3" /> : null}
        {state === "error" ? <path d="M0-4V0M0 3V3.1" /> : null}
        {state === "idle" ? <circle className="activity-orb-rest" r="2" /> : null}
      </svg>
    </div>
  );
}
