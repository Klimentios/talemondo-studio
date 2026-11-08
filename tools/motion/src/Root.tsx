// Every graphic is one composition; size, fps and length come from the render call (render.mjs → inputProps).
// Timing follows the edit's motion rules (docs/reference/EDIT_ENGINE.md): ease-out in 300–500 ms, out 200–300 ms,
// word stagger 40–70 ms, 80 px side margins at 1080 wide, text inside the 9:16 safe box (y 270–1248).
import React from 'react';
import { Composition } from 'remotion';
import { Captions } from './templates/Captions';
import { KineticTitle } from './templates/KineticTitle';
import { LowerThird } from './templates/LowerThird';
import { NameCard } from './templates/NameCard';
import { TitleCard } from './templates/TitleCard';

const TEMPLATES = { NameCard, KineticTitle, LowerThird, Captions, TitleCard } as const;

type Meta = { width?: number; height?: number; fps?: number; durationInFrames?: number };

export const Root: React.FC = () => (
  <>
    {Object.entries(TEMPLATES).map(([id, component]) => (
      <Composition
        key={id}
        id={id}
        component={component as React.FC<Record<string, unknown>>}
        width={1080}
        height={1920}
        fps={24}
        durationInFrames={72}
        defaultProps={{}}
        calculateMetadata={({ props }) => {
          const meta = props as Meta;
          return { width: meta.width ?? 1080, height: meta.height ?? 1920, fps: meta.fps ?? 24,
            durationInFrames: meta.durationInFrames ?? 72 };
        }}
      />
    ))}
  </>
);
