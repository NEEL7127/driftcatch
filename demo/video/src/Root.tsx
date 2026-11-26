/** Composition registry. */

import React from "react";
import { Composition } from "remotion";

import {
  DriftCatchGif,
  DriftCatchShort,
  DriftCatchVideo,
  SHORT_FRAMES,
  TOTAL_FRAMES,
} from "./DriftCatchVideo";
import { FPS, sec } from "./theme";

export const RemotionRoot: React.FC = () => (
  <>
    {/* LinkedIn / X native: 60s, 1080p landscape. */}
    <Composition
      id="Launch"
      component={DriftCatchVideo}
      durationInFrames={TOTAL_FRAMES}
      fps={FPS}
      width={1920}
      height={1080}
    />

    {/* The 30s cut, for feeds where attention is shorter. */}
    <Composition
      id="Short"
      component={DriftCatchShort}
      durationInFrames={SHORT_FRAMES}
      fps={FPS}
      width={1920}
      height={1080}
    />

    {/* Vertical, for Shorts and Reels. Same 30s cut, cropped frame. */}
    <Composition
      id="Vertical"
      component={DriftCatchShort}
      durationInFrames={SHORT_FRAMES}
      fps={FPS}
      width={1080}
      height={1920}
    />

    {/* The README loop: the catch on its own. */}
    <Composition
      id="DriftCatchGif"
      component={DriftCatchGif}
      durationInFrames={sec(10)}
      fps={FPS}
      width={1480}
      height={1040}
    />
  </>
);
