import React from 'react';

interface SmoothCollapseProps {
  open: boolean;
  children: React.ReactNode;
  className?: string;
  innerClassName?: string;
}

export default function SmoothCollapse({
  open,
  children,
  className = '',
  innerClassName = '',
}: SmoothCollapseProps) {
  if (!open) {
    return null;
  }

  return (
    <div className={`overflow-hidden ${className}`}>
      <div className={innerClassName}>{children}</div>
    </div>
  );
}
