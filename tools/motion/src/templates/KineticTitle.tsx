// Kinetic title: words slam in one by one (60 ms stagger) with a landing jolt; one word may take the accent.
import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { Base, FONTS, colors, exit, safe, unit } from '../style';

type Props = Base & { text?: string; highlight?: string; position?: 'center' | 'top' | 'bottom' };

export const KineticTitle: React.FC<Props> = (props) => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();
  const u = unit(width, height);
  const box = safe(width, height);
  const { accent, paper } = colors(props);
  const words = (props.text || 'TITLE').split(/\s+/).filter(Boolean);
  const out = exit(frame, fps, durationInFrames, 250);
  const stagger = 0.06 * fps;
  const landed = Math.min(frame / (stagger * words.length + 0.2 * fps), 1);
  const shake = landed < 1 ? Math.sin(frame * 3.1) * 6 * u * (1 - landed) : 0;
  const top = props.position === 'top' ? box.top : props.position === 'bottom' ? undefined : 0;
  return (
    <AbsoluteFill style={{ justifyContent: props.position === 'top' ? 'flex-start' : props.position === 'bottom' ? 'flex-end' : 'center',
      alignItems: 'center', paddingTop: top, paddingBottom: props.position === 'bottom' ? box.bottom : 0, opacity: out }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: `${4 * u}px ${24 * u}px`,
        maxWidth: width - box.left - box.right, transform: `translate(${shake}px, ${-shake / 2}px)` }}>
        {words.map((word, index) => {
          const s = spring({ frame: frame - index * stagger, fps, config: { damping: 12, stiffness: 260, mass: 0.5 } });
          const hot = props.highlight && word.replace(/[^\p{L}\p{N}]/gu, '').toLowerCase() === props.highlight.toLowerCase();
          return (
            <span key={index} style={{ display: 'inline-block', fontFamily: FONTS.display, fontWeight: 700,
              fontSize: 150 * u, lineHeight: 1, textTransform: 'uppercase', color: hot ? accent : paper,
              transform: `scale(${interpolate(s, [0, 1], [2.4, 1])})`, opacity: interpolate(s, [0, 0.2], [0, 1], { extrapolateRight: 'clamp' }),
              textShadow: `0 ${6 * u}px ${24 * u}px rgba(0,0,0,.55)` }}>{word}</span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
