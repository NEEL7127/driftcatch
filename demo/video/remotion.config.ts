import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
// Crisp text matters more than render speed here: the whole video is type.
Config.setChromiumOpenGlRenderer("angle");
