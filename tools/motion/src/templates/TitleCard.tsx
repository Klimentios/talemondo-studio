// Title card: the title rises and fades in, the subtitle follows, a short rule opens between them.
import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { Base, FONTS, colors, enter, exit, safe, unit } from '../style';

type Props = Base & { title?: string; subtitle?: string; backdrop?: boolean };

export const TitleCard: React.FC<Props> = (props) => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();
  const u = unit(width, height);
  const box = safe(width, height);
  const { accent, paper } = colors(props);
  const title = enter(frame, fps, 0, 500);
  const rule = enter(frame, fps, 0.25 * fps, 450);
  const sub = enter(frame, fps, 0.35 * fps, 450);
  const out = exit(frame, fps, durationInFrames, 300);
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', opacity: out,
      background: props.backdrop ? 'rgba(0,0,0,.45)' : undefined }}>
      <div style={{ maxWidth: width - box.left - box.right, textAlign: 'center' }}>
        <div style={{ fontFamily: FONTS.display, fontWeight: 700, fontSize: 132 * u, lineHeight: 1, color: paper,
          textTransform: 'uppercase', letterSpacing: 4 * u, opacity: title,
          transform: `translateY(${interpolate(title, [0, 1], [40 * u, 0])}px)`, textShadow: `0 ${6 * u}px ${28 * u}px rgba(0,0,0,.6)` }}>
          {props.title || 'TITLE'}
        </div>
        <div style={{ height: 6 * u, width: 220 * u, margin: `${22 * u}px auto`, background: accent, transform: `scaleX(${rule})` }} />
        {props.subtitle ? (
          <div style={{ fontFamily: FONTS.body, fontWeight: 600, fontSize: 48 * u, color: paper, opacity: sub,
            transform: `translateY(${interpolate(sub, [0, 1], [20 * u, 0])}px)`, textShadow: `0 ${3 * u}px ${14 * u}px rgba(0,0,0,.6)` }}>
            {props.subtitle}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
