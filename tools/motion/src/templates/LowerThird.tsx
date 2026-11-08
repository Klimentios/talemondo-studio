// Lower third: an accent bar slides in from the side, the name and the title follow it.
import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { Base, FONTS, colors, enter, exit, safe, unit } from '../style';

type Props = Base & { name?: string; title?: string };

export const LowerThird: React.FC<Props> = (props) => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();
  const u = unit(width, height);
  const box = safe(width, height);
  const { accent, ink, paper } = colors(props);
  const bar = enter(frame, fps, 0, 350);
  const name = enter(frame, fps, 0.12 * fps, 400);
  const title = enter(frame, fps, 0.2 * fps, 400);
  const out = exit(frame, fps, durationInFrames, 250);
  return (
    <AbsoluteFill style={{ opacity: out }}>
      <div style={{ position: 'absolute', left: box.left, bottom: box.bottom, display: 'flex', alignItems: 'stretch', gap: 18 * u }}>
        <div style={{ width: 12 * u, background: accent, transform: `scaleY(${bar})`, transformOrigin: 'bottom' }} />
        <div style={{ overflow: 'hidden' }}>
          <div style={{ padding: `${8 * u}px ${18 * u}px`, background: paper, color: ink, fontFamily: FONTS.heavy, fontWeight: 800,
            fontSize: 64 * u, lineHeight: 1.05, textTransform: 'uppercase',
            transform: `translateX(${interpolate(name, [0, 1], [-110, 0])}%)` }}>{props.name || 'Name'}</div>
          {props.title ? (
            <div style={{ marginTop: 8 * u, padding: `${6 * u}px ${18 * u}px`, background: 'rgba(11,11,13,.82)', color: paper,
              fontFamily: FONTS.body, fontWeight: 600, fontSize: 38 * u,
              transform: `translateX(${interpolate(title, [0, 1], [-110, 0])}%)` }}>{props.title}</div>
          ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
};
