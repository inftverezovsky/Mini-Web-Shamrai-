import React from 'react';

interface LogoTextProps {
  text?: string;
  className?: string;
  ariaLabel?: string;
  width?: number;
  height?: number;
}

const extrusionSteps = Array.from({ length: 10 }, (_, index) => index + 1);

export default function LogoText({
  text = 'SHAMRAI',
  className = '',
  ariaLabel,
  width = 390,
  height = 118,
}: LogoTextProps) {
  const label = ariaLabel ?? text;
  const titleId = React.useId();
  const gradientId = React.useId();
  const arcId = React.useId();
  const filterId = React.useId();
  const normalizedText = text.toUpperCase();

  return (
    <svg
      className={`logo-text ${className}`.trim()}
      viewBox="0 0 390 118"
      width={width}
      height={height}
      role="img"
      aria-labelledby={titleId}
      preserveAspectRatio="xMidYMid meet"
    >
      <title id={titleId}>{label}</title>
      <defs>
        <linearGradient id={gradientId} x1="0" x2="0" y1="20" y2="88" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#fffafc" />
          <stop offset="48%" stopColor="#ffe3ee" />
          <stop offset="100%" stopColor="#ffb8d8" />
        </linearGradient>
        <path id={arcId} d="M 24 72 Q 195 35 366 72" />
        <filter id={filterId} x="-12%" y="-18%" width="124%" height="145%">
          <feDropShadow dx="0" dy="8" stdDeviation="6" floodColor="#13051e" floodOpacity="0.72" />
        </filter>
      </defs>

      <g filter={`url(#${filterId})`} transform="translate(2 6) scale(1.07 1)">
        {extrusionSteps.map((step) => (
          <text
            key={step}
            className="logo-text__layer logo-text__layer--extrude"
            dx={step}
            dy={step}
            textAnchor="middle"
          >
            <textPath href={`#${arcId}`} startOffset="50%">
              {normalizedText}
            </textPath>
          </text>
        ))}

        <text className="logo-text__layer logo-text__layer--outer" textAnchor="middle">
          <textPath href={`#${arcId}`} startOffset="50%">
            {normalizedText}
          </textPath>
        </text>
        <text className="logo-text__layer logo-text__layer--main" textAnchor="middle">
          <textPath href={`#${arcId}`} startOffset="50%">
            {normalizedText}
          </textPath>
        </text>
        <text className="logo-text__layer logo-text__layer--inner" textAnchor="middle">
          <textPath href={`#${arcId}`} startOffset="50%">
            {normalizedText}
          </textPath>
        </text>
        <text className="logo-text__layer logo-text__layer--fill" fill={`url(#${gradientId})`} textAnchor="middle">
          <textPath href={`#${arcId}`} startOffset="50%">
            {normalizedText}
          </textPath>
        </text>
      </g>
    </svg>
  );
}
