import React from 'react';
import { BookmakerResponse } from '../schemas/schemas';
import { getBookmakerLogoSrc } from '../constants/bookmakers';
import { getSportIconSrc } from '../constants/sports';
import SportEmblem from './SportEmblem';

type LogoKind = 'bookmaker' | 'sport';
type LogoSize = 'tiny' | 'compact' | 'badge' | 'tile' | 'wide';

const sizeClasses: Record<LogoSize, string> = {
  tiny: 'w-4 h-4 rounded',
  compact: 'w-5 h-5 rounded-md',
  badge: 'w-10 h-8 rounded-lg',
  tile: 'w-9 h-7 rounded-lg',
  wide: 'w-20 h-9 rounded-lg',
};

interface LogoFrameProps {
  src: string;
  alt?: string;
  kind?: LogoKind;
  size?: LogoSize;
  active?: boolean;
  className?: string;
}

export function LogoFrame({
  src,
  alt = '',
  kind = 'sport',
  size = 'compact',
  active = false,
  className = '',
}: LogoFrameProps) {
  return (
    <span
      className={[
        'logo-frame',
        `logo-frame--${kind}`,
        active ? 'logo-frame--active' : '',
        sizeClasses[size],
        className,
      ].filter(Boolean).join(' ')}
    >
      {kind === 'sport' ? (
        <SportEmblem label={alt} />
      ) : (
        <img src={src} alt={alt} className="logo-frame__image" />
      )}
    </span>
  );
}

interface SportIconFrameProps {
  label: string;
  src?: string;
  size?: LogoSize;
  active?: boolean;
  className?: string;
}

export function SportIconFrame({
  label,
  src,
  size = 'compact',
  active,
  className,
}: SportIconFrameProps) {
  return (
    <LogoFrame
      src={src ?? getSportIconSrc(label)}
      alt={label}
      kind="sport"
      size={size}
      active={active}
      className={className}
    />
  );
}

interface BookmakerLogoFrameProps {
  bookmaker: Pick<BookmakerResponse, 'code' | 'name'>;
  size?: LogoSize;
  active?: boolean;
  className?: string;
}

export function BookmakerLogoFrame({
  bookmaker,
  size = 'wide',
  active,
  className,
}: BookmakerLogoFrameProps) {
  return (
    <LogoFrame
      src={getBookmakerLogoSrc(bookmaker)}
      alt={bookmaker.name}
      kind="bookmaker"
      size={size}
      active={active}
      className={className}
    />
  );
}
