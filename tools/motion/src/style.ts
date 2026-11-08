// Shared look: system fonts only (no download at render), one accent, safe box for vertical video.
import { Easing, interpolate } from 'remotion';

export const FONTS = {
  display: '"DIN Condensed", "Avenir Next Condensed", Impact, sans-serif',
  heavy: '"Avenir Next Condensed", "DIN Condensed", Impact, sans-serif',
  body: '"Avenir Next", "Helvetica Neue", Helvetica, sans-serif',
};

export type Base = { width?: number; height?: number; fps?: number; durationInFrames?: number; accent?: string;
  ink?: string; paper?: string };

export const colors = (p: Base) => ({ accent: p.accent || '#d6ff3a', ink: p.ink || '#0b0b0d', paper: p.paper || '#ffffff' });

/** Short side scale: sizes are written for 1080 wide and follow the frame. */
export const unit = (width: number, height: number) => Math.min(width, height) / 1080;

/** 9:16 safe box (TikTok/Reels UI): x 65–1015, y 270–1248 of 1080×1920; 16:9 keeps a 6% margin. */
export const safe = (width: number, height: number) => height > width
  ? { left: width * 0.075, right: width * 0.075, top: height * 0.14, bottom: height * 0.35 }
  : { left: width * 0.06, right: width * 0.06, top: height * 0.08, bottom: height * 0.1 };

export const easeOut = Easing.bezier(0.16, 1, 0.3, 1);
export const easeIn = Easing.bezier(0.7, 0, 0.84, 0);

/** 0 → 1 over `ms` from `startFrame`, eased out. */
export const enter = (frame: number, fps: number, startFrame: number, ms = 400) =>
  interpolate(frame, [startFrame, startFrame + (ms / 1000) * fps], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: easeOut });

/** 1 → 0 over the last `ms` of the graphic, eased in. */
export const exit = (frame: number, fps: number, total: number, ms = 250) =>
  interpolate(frame, [total - (ms / 1000) * fps, total], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: easeIn });
