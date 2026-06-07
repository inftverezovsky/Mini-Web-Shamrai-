import React from 'react';
import {
  Bike,
  Car,
  CircleDot,
  Club,
  Disc3,
  Dribbble,
  Dumbbell,
  FlagTriangleRight,
  Gamepad2,
  Goal,
  Hand,
  HandMetal,
  MountainSnow,
  PersonStanding,
  ShipWheel,
  Snowflake,
  Sparkles,
  Swords,
  Target,
  Timer,
  Trophy,
  Waves,
  Zap,
  type LucideIcon,
} from 'lucide-react';

type SportMark =
  | 'baseball'
  | 'bat'
  | 'beach'
  | 'belt'
  | 'crosshair'
  | 'dart'
  | 'eight'
  | 'football'
  | 'golf'
  | 'handball'
  | 'hockey'
  | 'oval'
  | 'paddle'
  | 'racing'
  | 'racket'
  | 'ribbon'
  | 'shuttle'
  | 'sled'
  | 'spark'
  | 'track'
  | 'volley'
  | 'water';

interface SportVisual {
  icon: LucideIcon;
  accent: string;
  accent2: string;
  mark: SportMark;
}

const SPORT_VISUALS: Record<string, SportVisual> = {
  'Автогонки': { icon: Car, accent: '#22d3ee', accent2: '#f59e0b', mark: 'racing' },
  'Ам. футбол': { icon: Goal, accent: '#fb923c', accent2: '#facc15', mark: 'oval' },
  'Бадминтон': { icon: Target, accent: '#38bdf8', accent2: '#fef08a', mark: 'shuttle' },
  'Баскетбол': { icon: Dribbble, accent: '#fb923c', accent2: '#fed7aa', mark: 'volley' },
  'Бейсбол': { icon: CircleDot, accent: '#f8fafc', accent2: '#fb7185', mark: 'baseball' },
  'Бильярд': { icon: CircleDot, accent: '#34d399', accent2: '#f8fafc', mark: 'eight' },
  'Бокс': { icon: HandMetal, accent: '#fb7185', accent2: '#facc15', mark: 'spark' },
  'Велоспорт': { icon: Bike, accent: '#2dd4bf', accent2: '#a7f3d0', mark: 'track' },
  'Вод. поло': { icon: Waves, accent: '#38bdf8', accent2: '#facc15', mark: 'water' },
  'Водные виды': { icon: ShipWheel, accent: '#22d3ee', accent2: '#60a5fa', mark: 'water' },
  'Волейбол': { icon: CircleDot, accent: '#e2e8f0', accent2: '#94a3b8', mark: 'volley' },
  'Гандбол': { icon: Hand, accent: '#f97316', accent2: '#fde68a', mark: 'handball' },
  'Гимнастика': { icon: PersonStanding, accent: '#c084fc', accent2: '#f0abfc', mark: 'ribbon' },
  'Гольф': { icon: FlagTriangleRight, accent: '#84cc16', accent2: '#f8fafc', mark: 'golf' },
  'Дартс': { icon: Target, accent: '#f43f5e', accent2: '#22d3ee', mark: 'dart' },
  'Другие': { icon: Trophy, accent: '#f59e0b', accent2: '#fef08a', mark: 'spark' },
  'Единоборства': { icon: Swords, accent: '#e879f9', accent2: '#facc15', mark: 'belt' },
  'Киберспорт': { icon: Gamepad2, accent: '#60a5fa', accent2: '#f472b6', mark: 'spark' },
  'Коньки': { icon: Snowflake, accent: '#7dd3fc', accent2: '#f8fafc', mark: 'hockey' },
  'Крикет': { icon: Club, accent: '#f97316', accent2: '#f8fafc', mark: 'bat' },
  'Л/Атл': { icon: Timer, accent: '#fb7185', accent2: '#fde68a', mark: 'track' },
  'Лыжи/Биатлон': { icon: MountainSnow, accent: '#93c5fd', accent2: '#f8fafc', mark: 'crosshair' },
  'Н/Т': { icon: Disc3, accent: '#fb7185', accent2: '#f8fafc', mark: 'paddle' },
  'Пляж. футб': { icon: Goal, accent: '#fbbf24', accent2: '#38bdf8', mark: 'beach' },
  'Регби': { icon: Dumbbell, accent: '#a3e635', accent2: '#fb923c', mark: 'oval' },
  'Сани/Бобслей': { icon: Snowflake, accent: '#93c5fd', accent2: '#f8fafc', mark: 'sled' },
  'Теннис': { icon: Target, accent: '#bef264', accent2: '#f8fafc', mark: 'racket' },
  'Футбол': { icon: Goal, accent: '#34d399', accent2: '#f8fafc', mark: 'football' },
  'Футзал': { icon: Goal, accent: '#2dd4bf', accent2: '#facc15', mark: 'football' },
  'Хоккей': { icon: Zap, accent: '#60a5fa', accent2: '#f8fafc', mark: 'hockey' },
};

const FALLBACK_VISUAL: SportVisual = {
  icon: Sparkles,
  accent: '#f472b6',
  accent2: '#22d3ee',
  mark: 'spark',
};

interface SportEmblemProps {
  label: string;
}

export default function SportEmblem({ label }: SportEmblemProps) {
  const visual = SPORT_VISUALS[label] ?? FALLBACK_VISUAL;
  const Icon = visual.icon;

  return (
    <span
      className="sport-emblem"
      style={
        {
          '--sport-accent': visual.accent,
          '--sport-accent-2': visual.accent2,
        } as React.CSSProperties
      }
    >
      <span className="sport-emblem__orb" />
      <Icon className="sport-emblem__icon" strokeWidth={2.35} />
      <SportGlyph mark={visual.mark} />
    </span>
  );
}

function SportGlyph({ mark }: { mark: SportMark }) {
  return (
    <svg
      className={`sport-emblem__glyph sport-emblem__glyph--${mark}`}
      viewBox="0 0 64 64"
      aria-hidden="true"
    >
      <g fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="5">
        {mark === 'baseball' && (
          <>
            <circle cx="32" cy="32" r="20" />
            <path d="M21 16c7 9 7 23 0 32M43 16c-7 9-7 23 0 32" />
          </>
        )}
        {mark === 'bat' && (
          <>
            <path d="M17 50 45 16" />
            <path d="m42 13 8 7-5 6-8-7z" />
            <circle cx="18" cy="49" r="5" />
          </>
        )}
        {mark === 'beach' && (
          <>
            <path d="M10 42c7-6 14-6 21 0s14 6 21 0" />
            <circle cx="38" cy="24" r="11" />
            <path d="M38 13c-2 8 1 14 9 18M27 25c7-1 13 2 17 9" />
          </>
        )}
        {mark === 'belt' && (
          <>
            <path d="M12 36h40" />
            <path d="M23 27h18l-5 9h-18z" />
            <path d="m38 36 10 9M26 36l-10 9" />
          </>
        )}
        {mark === 'crosshair' && (
          <>
            <circle cx="32" cy="32" r="18" />
            <path d="M32 12v12M32 40v12M12 32h12M40 32h12" />
          </>
        )}
        {mark === 'dart' && (
          <>
            <path d="M16 48 45 19" />
            <path d="M42 16h12l-7 7" />
            <path d="M34 27 48 41" />
          </>
        )}
        {mark === 'eight' && (
          <>
            <circle cx="32" cy="32" r="20" fill="currentColor" stroke="none" />
            <circle cx="32" cy="32" r="10" fill="rgba(255,255,255,0.92)" stroke="none" />
            <path d="M28 28h8M28 34h8" stroke="rgba(15,23,42,0.92)" strokeWidth="4" />
          </>
        )}
        {mark === 'football' && (
          <>
            <circle cx="32" cy="32" r="20" />
            <path d="m32 20 11 8-4 13H25l-4-13z" />
            <path d="M21 28 12 24M43 28l9-4M25 41l-5 9M39 41l5 9" />
          </>
        )}
        {mark === 'golf' && (
          <>
            <path d="M24 50V15l20 8-20 8" />
            <circle cx="44" cy="48" r="6" />
            <path d="M14 52h26" />
          </>
        )}
        {mark === 'handball' && (
          <>
            <path d="M18 42V25M27 42V18M36 42V22M45 42V29" />
            <path d="M16 42c5 10 30 10 34 0" />
            <circle cx="45" cy="18" r="8" />
          </>
        )}
        {mark === 'hockey' && (
          <>
            <path d="M20 13v29c0 6 5 10 12 10h13" />
            <path d="M45 52h10" />
            <path d="M16 50h11" />
          </>
        )}
        {mark === 'oval' && (
          <>
            <ellipse cx="32" cy="32" rx="24" ry="14" transform="rotate(-18 32 32)" />
            <path d="M25 26 39 36M28 30l4-5M32 33l4-5M36 36l4-5" />
          </>
        )}
        {mark === 'paddle' && (
          <>
            <circle cx="27" cy="27" r="16" />
            <path d="m39 39 13 13" />
            <circle cx="48" cy="19" r="5" />
          </>
        )}
        {mark === 'racing' && (
          <>
            <path d="M15 16v34" />
            <path d="M16 17c11-7 20 7 32 0v23c-12 7-21-7-32 0z" />
            <path d="M24 15v30M33 19v30M42 17v30M16 28h32" />
          </>
        )}
        {mark === 'racket' && (
          <>
            <ellipse cx="28" cy="24" rx="16" ry="20" transform="rotate(34 28 24)" />
            <path d="m38 39 14 13M18 22h28M21 14l25 30" />
            <circle cx="47" cy="16" r="5" />
          </>
        )}
        {mark === 'ribbon' && (
          <>
            <path d="M16 40c12-28 36 7 18 14-12 5-20-8-6-18 8-6 19-6 28-1" />
            <circle cx="14" cy="41" r="4" fill="currentColor" stroke="none" />
          </>
        )}
        {mark === 'shuttle' && (
          <>
            <path d="M18 14 42 38" />
            <path d="m39 35 10 10-5 5-10-10z" />
            <path d="M18 14h22M18 14v22M25 21h22M25 21v22" />
          </>
        )}
        {mark === 'sled' && (
          <>
            <path d="M18 18v24c0 5 4 8 9 8h26" />
            <path d="M14 52h40M26 42l-8 10M44 42l-8 10" />
          </>
        )}
        {mark === 'spark' && (
          <>
            <path d="M32 10 37 27l17 5-17 5-5 17-5-17-17-5 17-5z" />
            <path d="M50 12v10M45 17h10" />
          </>
        )}
        {mark === 'track' && (
          <>
            <path d="M12 44c12 10 28 10 40 0" />
            <path d="M17 34c9 8 21 8 30 0" />
            <path d="M22 24c6 5 14 5 20 0" />
          </>
        )}
        {mark === 'volley' && (
          <>
            <circle cx="32" cy="32" r="21" />
            <path d="M32 11c-2 11 2 18 12 23M15 22c10 0 18 4 24 13M18 47c4-11 12-17 24-18" />
          </>
        )}
        {mark === 'water' && (
          <>
            <path d="M10 42c7-6 14-6 21 0s14 6 21 0" />
            <path d="M12 52c7-5 14-5 21 0s14 5 21 0" />
            <circle cx="42" cy="22" r="9" />
          </>
        )}
      </g>
    </svg>
  );
}
