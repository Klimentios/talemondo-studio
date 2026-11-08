// Word captions (pop style): pages of a few words, the spoken word pops and takes the accent.
// words: [{ text, start, end }] in seconds from the graphic's start.
import React from 'react';
import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { Base, FONTS, colors, safe, unit } from '../style';

type Word = { text: string; start: number; end: number };
type Props = Base & { words?: Word[]; perPage?: number };

export const Captions: React.FC<Props> = (props) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const u = unit(width, height);
  const box = safe(width, height);
  const { accent, paper, ink } = colors(props);
  const words = props.words || [];
  const per = Math.max(1, props.perPage ?? 3);
  const now = frame / fps;
  const index = words.findIndex((word, i) => now >= word.start && now < (words[i + 1]?.start ?? word.end + 0.3));
  if (index < 0) return null;
  const first = Math.floor(index / per) * per;
  const page = words.slice(first, first + per);
  return (
    <AbsoluteFill style={{ justifyContent: 'flex-end', alignItems: 'center', paddingBottom: box.bottom + 20 * u }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: `${6 * u}px ${18 * u}px`,
        maxWidth: width - box.left - box.right }}>
        {page.map((word, i) => {
          const active = first + i === index;
          const pop = spring({ frame: frame - Math.round(word.start * fps), fps, config: { damping: 14, stiffness: 300, mass: 0.4 } });
          return (
            <span key={first + i} style={{ display: 'inline-block', fontFamily: FONTS.heavy, fontWeight: 800, fontSize: 80 * u,
              lineHeight: 1.1, textTransform: 'uppercase', color: active ? ink : paper,
              background: active ? accent : 'transparent', padding: `0 ${10 * u}px`, borderRadius: 10 * u,
              transform: `scale(${active ? 0.85 + 0.15 * pop : 1})`, opacity: now >= word.start ? 1 : 0.55,
              WebkitTextStroke: active ? undefined : `${3 * u}px ${ink}`, paintOrder: 'stroke fill' }}>{word.text}</span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
