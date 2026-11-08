// Freeze-frame character intro (Guy Ritchie): the name slams in on a tilted slab, the line types on,
// an accent rule wipes under it. Sits over the frozen frame (edit sheet freeze_end).
import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { Base, FONTS, colors, enter, exit, safe, unit } from '../style';

type Props = Base & { name?: string; line?: string; tag?: string; side?: 'left' | 'right'; tilt?: number };

export const NameCard: React.FC<Props> = (props) => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();
  const u = unit(width, height);
  const box = safe(width, height);
  const { accent, ink, paper } = colors(props);
  const right = props.side === 'right';
  const tilt = props.tilt ?? -4;
  const slam = spring({ frame, fps, config: { damping: 11, stiffness: 220, mass: 0.6 } });
  const scale = interpolate(slam, [0, 1], [1.9, 1]);
  const slab = enter(frame, fps, 0, 300);
  const out = exit(frame, fps, durationInFrames, 250);
  const line = props.line || '';
  const typed = Math.round(interpolate(frame, [0.35 * fps, 0.35 * fps + line.length * 0.035 * fps], [0, line.length],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }));
  const rule = enter(frame, fps, 0.25 * fps, 450);
  const jolt = frame < 0.25 * fps ? Math.sin(frame * 2.7) * 10 * u * (1 - frame / (0.25 * fps)) : 0;
  return (
    <AbsoluteFill style={{ opacity: out }}>
      <div style={{ position: 'absolute', [right ? 'right' : 'left']: box.left, bottom: box.bottom + 40 * u,
        transform: `rotate(${tilt}deg) translateX(${jolt}px)`, transformOrigin: right ? 'right bottom' : 'left bottom',
        textAlign: right ? 'right' : 'left', maxWidth: width - box.left - box.right }}>
        {props.tag ? (
          <div><div style={{ display: 'inline-block', marginBottom: 14 * u, padding: `${6 * u}px ${14 * u}px`, background: accent,
            color: ink, fontFamily: FONTS.heavy, fontWeight: 800, fontSize: 30 * u, letterSpacing: 3 * u,
            textTransform: 'uppercase', opacity: slab }}>{props.tag}</div></div>
        ) : null}
        <div style={{ position: 'relative', display: 'inline-block' }}>
          <div style={{ position: 'absolute', inset: `${-10 * u}px ${-24 * u}px`, background: ink,
            transform: `scaleX(${slab})`, transformOrigin: right ? 'right' : 'left', opacity: 0.92 }} />
          <div style={{ position: 'relative', fontFamily: FONTS.display, fontWeight: 700, fontSize: 168 * u, lineHeight: 0.95,
            color: paper, textTransform: 'uppercase', letterSpacing: 2 * u, transform: `scale(${scale})`,
            transformOrigin: right ? 'right center' : 'left center', textShadow: `${6 * u}px ${6 * u}px 0 ${accent}` }}>
            {props.name || 'NAME'}
          </div>
        </div>
        <div style={{ height: 10 * u, marginTop: 18 * u, background: accent, transform: `scaleX(${rule})`,
          transformOrigin: right ? 'right' : 'left' }} />
        {line ? (
          <div style={{ marginTop: 16 * u, fontFamily: FONTS.body, fontWeight: 700, fontSize: 46 * u, color: paper,
            textShadow: `0 ${3 * u}px ${12 * u}px rgba(0,0,0,.65)`, letterSpacing: 0.5 * u }}>
            {line.slice(0, typed)}<span style={{ opacity: typed < line.length ? 1 : 0, color: accent }}>▌</span>
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
