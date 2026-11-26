/**
 * The timeline.
 *
 * Scene boundaries are declared once, in seconds, so they can be read against
 * SCRIPT.md without arithmetic. Each scene renders from its own frame 0.
 */

import React from "react";
import { AbsoluteFill, Sequence } from "remotion";

import {
  SceneCTA,
  SceneCatch,
  SceneDamage,
  SceneDiff,
  SceneLearn,
  SceneRecord,
  SceneReveal,
  SceneThesis,
} from "./scenes/Scenes";
import { color, sec } from "./theme";

type Beat = {
  at: number;
  seconds: number;
  Component: React.FC;
};

/** The full 60-second cut, matching SCRIPT.md beat for beat. */
export const BEATS: Beat[] = [
  { at: 0, seconds: 5, Component: SceneDiff },
  { at: 5, seconds: 8, Component: SceneDamage },
  { at: 13, seconds: 5, Component: SceneThesis },
  { at: 18, seconds: 6, Component: SceneReveal },
  { at: 24, seconds: 9, Component: SceneRecord },
  { at: 33, seconds: 10, Component: SceneCatch },
  { at: 43, seconds: 11, Component: SceneLearn },
  { at: 54, seconds: 6, Component: SceneCTA },
];

/**
 * The 30-second cut: pain, the catch, the call to action.
 *
 * Not a trim of the long one - the setup scenes are dropped entirely and the
 * rest is re-timed, because a cut-down that merely plays faster reads as
 * rushed rather than tight.
 */
export const SHORT_BEATS: Beat[] = [
  { at: 0, seconds: 4, Component: SceneDiff },
  { at: 4, seconds: 7, Component: SceneDamage },
  { at: 11, seconds: 4, Component: SceneThesis },
  { at: 15, seconds: 10, Component: SceneCatch },
  { at: 25, seconds: 5, Component: SceneCTA },
];

export const TOTAL_FRAMES = sec(60);
export const SHORT_FRAMES = sec(30);

const Timeline: React.FC<{ beats: Beat[] }> = ({ beats }) => (
  <AbsoluteFill style={{ background: color.bg }}>
    {beats.map(({ at, seconds, Component }) => (
      <Sequence
        key={`${Component.name}-${at}`}
        from={sec(at)}
        durationInFrames={sec(seconds)}
      >
        <Component />
      </Sequence>
    ))}
  </AbsoluteFill>
);

export const DriftCatchVideo: React.FC = () => <Timeline beats={BEATS} />;

export const DriftCatchShort: React.FC = () => <Timeline beats={SHORT_BEATS} />;

/**
 * The README loop: the catch, on its own.
 *
 * A passing replay followed by a caught regression is the single clearest
 * thing this project does, and it is the only scene that earns an autoplaying
 * GIF at the top of a repository.
 */
export const DriftCatchGif: React.FC = () => (
  <AbsoluteFill style={{ background: color.bg }}>
    <SceneCatch />
  </AbsoluteFill>
);
