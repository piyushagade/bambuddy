import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';

export interface MarqueeTextProps {
  children: ReactNode;
  className?: string;
  innerClassName?: string;
  align?: 'left' | 'right';
  title?: string;
}

/**
 * Single-line text container that smoothly scrolls back and forth (marquee)
 * whenever its content exceeds the available width, while keeping a single
 * DOM text node for accessibility and tests.
 */
export function MarqueeText({
  children,
  className = '',
  innerClassName = '',
  align = 'left',
  title,
}: MarqueeTextProps) {
  const containerRef = useRef<HTMLSpanElement>(null);
  const contentRef = useRef<HTMLSpanElement>(null);
  const [overflowPx, setOverflowPx] = useState(0);

  useEffect(() => {
    const container = containerRef.current;
    const content = contentRef.current;
    if (!container || !content) return;

    let cancelled = false;
    const updateOverflow = () => {
      if (cancelled) return;
      const diff = Math.ceil(content.scrollWidth - container.clientWidth);
      setOverflowPx(diff > 2 ? diff : 0);
    };

    updateOverflow();
    const rafId = requestAnimationFrame(updateOverflow);
    const timerId = window.setTimeout(updateOverflow, 150);

    if (typeof document !== 'undefined' && 'fonts' in document && document.fonts?.ready) {
      void document.fonts.ready.then(updateOverflow).catch(() => {});
    }

    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(updateOverflow);
      observer.observe(container);
      observer.observe(content);
    }

    window.addEventListener('resize', updateOverflow);
    return () => {
      cancelled = true;
      cancelAnimationFrame(rafId);
      window.clearTimeout(timerId);
      observer?.disconnect();
      window.removeEventListener('resize', updateOverflow);
    };
  }, [children]);

  const durationSec = Math.max(5.5, Math.min(14, overflowPx / 14 + 3.5));

  return (
    <span
      ref={containerRef}
      className={`inline-flex overflow-hidden min-w-0 align-bottom ${
        overflowPx === 0 && align === 'right' ? 'justify-end' : 'justify-start'
      } ${className}`}
      title={title}
    >
      <span
        ref={contentRef}
        className={`inline-block whitespace-nowrap shrink-0 ${
          overflowPx > 0 ? 'animate-marquee-bounce' : ''
        } ${innerClassName}`}
        style={
          overflowPx > 0
            ? ({
                '--marquee-offset': `-${overflowPx}px`,
                '--marquee-duration': `${durationSec.toFixed(1)}s`,
              } as CSSProperties)
            : undefined
        }
      >
        {children}
      </span>
    </span>
  );
}
