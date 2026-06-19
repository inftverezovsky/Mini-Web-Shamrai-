import React from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';

const collapseEase = [0.16, 1, 0.3, 1] as const;

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
  const reduceMotion = useReducedMotion();

  if (reduceMotion) {
    return open ? <div className={className}>{children}</div> : null;
  }

  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.div
          key="smooth-collapse"
          initial={{ height: 0, opacity: 0, y: -4 }}
          animate={{ height: 'auto', opacity: 1, y: 0 }}
          exit={{ height: 0, opacity: 0, y: -4 }}
          transition={{
            height: { duration: 0.2, ease: collapseEase },
            opacity: { duration: 0.14, ease: 'easeOut' },
            y: { duration: 0.18, ease: collapseEase },
          }}
          className={`overflow-hidden ${className}`}
        >
          <div className={innerClassName}>{children}</div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
