/**
 * Shared primitives.
 *
 * Every animation here is driven by the frame number rather than by CSS
 * transitions, because Remotion renders frames out of order - a CSS animation
 * would produce a different result depending on when the frame was rendered.
 */

import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

import { color, font, size } from "./theme";

// ---------------------------------------------------------------- easing

/** Fade and rise, the default entrance for anything that appears. */
export const useEnter = (startFrame: number, durationFrames = 14) => {
  const frame = useCurrentFrame();
  const progress = interpolate(
    frame,
    [startFrame, startFrame + durationFrames],
    [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );
  return {
    opacity: progress,
    transform: `translateY(${interpolate(progress, [0, 1], [14, 0])}px)`,
  };
};

/** A spring, for the few things that should feel physical rather than faded. */
export const usePop = (startFrame: number) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const value = spring({
    frame: frame - startFrame,
    fps,
    config: { damping: 14, mass: 0.6 },
  });
  return { opacity: Math.min(1, value * 1.6), transform: `scale(${0.94 + value * 0.06})` };
};

// ------------------------------------------------------------ typewriter

/**
 * Reveal text one character at a time.
 *
 * Speed is characters-per-frame rather than a fixed duration, so a long block
 * does not type faster than a short one - the viewer's reading speed is the
 * constant, not the scene length.
 */
export const useTyped = (
  text: string,
  startFrame: number,
  charsPerFrame = 2.4
): { shown: string; done: boolean } => {
  const frame = useCurrentFrame();
  const elapsed = Math.max(0, frame - startFrame);
  const count = Math.floor(elapsed * charsPerFrame);
  return { shown: text.slice(0, count), done: count >= text.length };
};

export const Cursor: React.FC<{ visible?: boolean }> = ({ visible = true }) => {
  const frame = useCurrentFrame();
  if (!visible) return null;
  // 15-frame period: fast enough to read as a cursor, slow enough not to strobe.
  const on = Math.floor(frame / 15) % 2 === 0;
  return (
    <span
      style={{
        display: "inline-block",
        width: "0.55em",
        height: "1.05em",
        marginLeft: 2,
        verticalAlign: "text-bottom",
        background: on ? color.accent : "transparent",
      }}
    />
  );
};

// ---------------------------------------------------------------- chrome

/** A terminal or editor window. The title bar sells that this is real. */
export const Window: React.FC<{
  title: string;
  children: React.ReactNode;
  width?: number | string;
  style?: React.CSSProperties;
}> = ({ title, children, width = 1360, style }) => (
  <div
    style={{
      width,
      borderRadius: 14,
      overflow: "hidden",
      // Flex children shrink by default, and this one is inside a centring
      // column. Without this the window is squeezed to fit and overflow:hidden
      // silently crops the last line - which ate the drift underline, the one
      // mark the whole scene exists to show.
      flexShrink: 0,
      background: color.surface,
      border: `1px solid ${color.line}`,
      boxShadow: "0 30px 90px rgba(0,0,0,0.55)",
      ...style,
    }}
  >
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "14px 18px",
        background: color.surfaceHi,
        borderBottom: `1px solid ${color.line}`,
      }}
    >
      {["#FF5F56", "#FFBD2E", "#27C93F"].map((dot) => (
        <span
          key={dot}
          style={{ width: 13, height: 13, borderRadius: "50%", background: dot }}
        />
      ))}
      <span
        style={{
          marginLeft: 10,
          color: color.dim,
          font: `500 20px ${font.sans}`,
          letterSpacing: 0.2,
        }}
      >
        {title}
      </span>
    </div>
    <div
      style={{
        // Extra bottom padding: the drift underline is the last line of the
        // catch scene and tall glyphs were being cropped against the window
        // edge, which removed the one mark the scene exists to show.
        padding: "26px 30px 48px",
        font: `${size.code}px ${font.mono}`,
        lineHeight: 1.62,
      }}
    >
      {children}
    </div>
  </div>
);

// --------------------------------------------------------------- captions

/** The line that carries the story for a muted viewer. */
export const Caption: React.FC<{
  children: React.ReactNode;
  startFrame: number;
  /** Fade out here, so two captions never share the frame. */
  endFrame?: number;
  tone?: keyof typeof color;
  bottom?: number;
}> = ({ children, startFrame, endFrame, tone = "text", bottom = 70 }) => {
  const frame = useCurrentFrame();
  const enter = useEnter(startFrame);
  const exit =
    endFrame === undefined
      ? 1
      : interpolate(frame, [endFrame, endFrame + 8], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });

  return (
    <div
      style={{
        position: "absolute",
        bottom,
        left: 0,
        right: 0,
        textAlign: "center",
        padding: "0 100px",
        color: color[tone],
        font: `600 ${size.caption}px ${font.sans}`,
        letterSpacing: -0.4,
        ...enter,
        opacity: enter.opacity * exit,
      }}
    >
      {children}
    </div>
  );
};

export const Scene: React.FC<{
  children: React.ReactNode;
  center?: boolean;
  /**
   * Room reserved at the bottom for captions.
   *
   * Captions are absolutely positioned, so without this the content block
   * centres against the whole frame and the last line of a terminal ends up
   * underneath the caption - which is how the drift underline got clipped.
   */
  captionSpace?: number;
}> = ({ children, center = true, captionSpace = 190 }) => (
  <div
    style={{
      position: "absolute",
      inset: 0,
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: center ? "center" : "flex-start",
      paddingBottom: captionSpace,
      background: color.bg,
    }}
  >
    {children}
  </div>
);

// ------------------------------------------------------------ trajectory

/** One agent trajectory as a row of nodes. */
export const Trajectory: React.FC<{
  steps: string[];
  highlightLast?: keyof typeof color;
  startFrame: number;
  perStep?: number;
  scale?: number;
}> = ({ steps, highlightLast, startFrame, perStep = 7, scale = 1 }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 14 * scale, flexWrap: "wrap" }}>
    {steps.map((step, index) => {
      const isLast = index === steps.length - 1;
      const tone = isLast && highlightLast ? color[highlightLast] : color.text;
      return (
        <React.Fragment key={step}>
          {index > 0 && (
            <Arrow startFrame={startFrame + index * perStep - 3} scale={scale} />
          )}
          <Node
            label={step}
            tone={tone}
            startFrame={startFrame + index * perStep}
            emphasise={Boolean(isLast && highlightLast)}
            scale={scale}
          />
        </React.Fragment>
      );
    })}
  </div>
);

const Node: React.FC<{
  label: string;
  tone: string;
  startFrame: number;
  emphasise: boolean;
  scale: number;
}> = ({ label, tone, startFrame, emphasise, scale }) => {
  const frame = useCurrentFrame();
  const pop = usePop(startFrame);
  // One pulse, a beat after it lands, so the eye is pulled to the changed node
  // without the frame ever feeling busy.
  const since = frame - startFrame;
  const pulse =
    emphasise && since > 10 && since < 34
      ? 1 + 0.045 * Math.sin(((since - 10) / 24) * Math.PI)
      : 1;

  return (
    <div
      style={{
        padding: `${14 * scale}px ${24 * scale}px`,
        borderRadius: 10 * scale,
        background: emphasise ? "rgba(248,81,73,0.12)" : color.surface,
        border: `${emphasise ? 2 : 1}px solid ${emphasise ? tone : color.line}`,
        color: tone,
        font: `${28 * scale}px ${font.mono}`,
        whiteSpace: "nowrap",
        ...pop,
        transform: `${pop.transform} scale(${pulse})`,
      }}
    >
      {label}
    </div>
  );
};

const Arrow: React.FC<{ startFrame: number; scale: number }> = ({ startFrame, scale }) => {
  const enter = useEnter(startFrame, 8);
  return (
    <span style={{ color: color.dim, font: `${26 * scale}px ${font.mono}`, ...enter }}>→</span>
  );
};
