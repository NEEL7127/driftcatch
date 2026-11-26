/**
 * Fonts are loaded, not assumed.
 *
 * The first render of this video came out in a fallback face because
 * JetBrains Mono is not installed on the machine doing the rendering - and the
 * giveaway was a malformed `^` in the drift underline. A video whose
 * typography depends on the renderer's installed fonts will look different on
 * every machine, which for a tool about determinism would be a poor joke.
 */

import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadJetBrains } from "@remotion/google-fonts/JetBrainsMono";

const mono = loadJetBrains();
const sans = loadInter();

export const monoFamily = mono.fontFamily;
export const sansFamily = sans.fontFamily;

/** Awaited by Remotion before the first frame is rendered. */
export const fontsReady = Promise.all([mono.waitUntilDone(), sans.waitUntilDone()]);
