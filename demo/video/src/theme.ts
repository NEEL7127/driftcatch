/**
 * One source of truth for the look.
 *
 * The palette is deliberately close to a real dark editor rather than a brand
 * deck: the viewer should feel they are looking at their own terminal, because
 * the whole argument of the video is "this happens to you".
 */

import { monoFamily, sansFamily } from "./fonts";

export const color = {
  bg: "#0B0E14",
  surface: "#141921",
  surfaceHi: "#1C2230",
  line: "#2A3242",
  text: "#E6EDF3",
  dim: "#7D8590",
  accent: "#4D9FFF",
  pass: "#3FB950",
  drift: "#F85149",
  warn: "#D29922",
} as const;

export const font = {
  mono: `${monoFamily}, "Cascadia Code", Consolas, ui-monospace, monospace`,
  sans: `${sansFamily}, "Segoe UI", system-ui, -apple-system, sans-serif`,
} as const;

/**
 * Minimum readable sizes at 1080p, scaled up for the vertical cut.
 *
 * Code never goes below `code` px. If a block does not fit, the block gets
 * shorter - it does not get smaller. Someone is watching this on a phone.
 */
export const size = {
  code: 30,
  caption: 46,
  thesis: 82,
  wordmark: 110,
} as const;

export const FPS = 30;

/** Seconds to frames, so scene timings read like the script. */
export const sec = (seconds: number): number => Math.round(seconds * FPS);
