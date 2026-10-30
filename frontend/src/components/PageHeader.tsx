import { useState, useRef, useEffect, type ReactNode } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { Button } from './Button';

export interface PageHeaderProps {
  icon?: ReactNode;
  title: ReactNode;
  titleExtra?: ReactNode;
  subtitle?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({
  icon,
  title,
  titleExtra,
  subtitle,
  children,
  actions,
  className = '',
}: PageHeaderProps) {
  return (
    <div
      className={`flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between ${className}`.trim()}
    >
      <div className="min-w-0">
        <h1 className="text-2xl font-bold text-white flex items-center gap-3 flex-wrap">
          {icon}
          {title}
          {titleExtra}
        </h1>
        {subtitle && <p className="text-bambu-gray mt-1">{subtitle}</p>}
        {children}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          {actions}
        </div>
      )}
    </div>
  );
}

export interface HeaderMoreMenuProps {
  label?: string;
  children: ReactNode;
}

export function HeaderMoreMenu({
  label = 'More actions',
  children,
}: HeaderMoreMenuProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const handlePointerDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  return (
    <div ref={containerRef} className="relative">
      <Button
        type="button"
        variant="secondary"
        size="sm"
        onClick={() => setIsOpen((prev) => !prev)}
        title={label}
        aria-label={label}
        aria-expanded={isOpen}
      >
        <MoreHorizontal className="w-4 h-4" />
      </Button>
      <div
        data-testid="header-more-menu"
        className={
          isOpen
            ? 'absolute right-0 top-full mt-1.5 min-w-52 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl z-30 p-1.5 flex flex-col gap-1 [&>*]:w-full [&>*]:!justify-start'
            : 'hidden'
        }
        onClick={() => setIsOpen(false)}
      >
        {children}
      </div>
    </div>
  );
}
