import { useEffect } from 'react';
import type { ReactNode, MouseEvent } from 'react';
import { X } from 'lucide-react';

export type PopupModalSize = 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | '4xl' | '5xl';

const MAX_WIDTH_CLASSES: Record<PopupModalSize, string> = {
  sm: 'sm:max-w-sm',
  md: 'sm:max-w-md',
  lg: 'sm:max-w-lg',
  xl: 'sm:max-w-xl',
  '2xl': 'sm:max-w-2xl',
  '3xl': 'sm:max-w-3xl',
  '4xl': 'sm:max-w-4xl',
  '5xl': 'sm:max-w-5xl',
};

export interface PopupModalProps {
  /** Called when the user clicks the backdrop, presses Escape, or clicks the X button */
  onClose?: () => void;
  /** Modal title (rendered in the sticky header when provided) */
  title?: ReactNode;
  /** Optional subtitle below the title */
  subtitle?: ReactNode;
  /** Optional leading icon in the header */
  icon?: ReactNode;
  /** Optional action buttons rendered in the header to the left of the close button */
  headerActions?: ReactNode;
  /** Optional fixed toolbar / filter / bulk-action section rendered below the header */
  subHeader?: ReactNode;
  /** Optional fixed footer rendered at the bottom of the modal */
  footer?: ReactNode;
  /** Maximum width preset (always bounded by viewport width with padding) */
  maxWidth?: PopupModalSize;
  /** Tailwind z-index class for stacking nested modals (default: 'z-50') */
  zIndex?: string;
  /** Whether clicking the backdrop closes the modal (default: true) */
  closeOnBackdrop?: boolean;
  /** Whether pressing Escape closes the modal (default: true) */
  closeOnEscape?: boolean;
  /** Whether to show the top-right X close button in the header (default: true) */
  showCloseButton?: boolean;
  /** Classes applied to the scrollable body container (default: 'p-4') */
  bodyClassName?: string;
  /** Additional classes applied to the modal dialog box */
  className?: string;
  /** Main scrollable content */
  children: ReactNode;
}

/**
 * Reusable viewport-bounded popup modal shell.
 * Guarantees consistent screen-edge padding, maximum width/height constraints,
 * fixed header/subHeader/footer regions, and an internal scrollable content area.
 */
export function PopupModal({
  onClose,
  title,
  subtitle,
  icon,
  headerActions,
  subHeader,
  footer,
  maxWidth = 'md',
  zIndex = 'z-50',
  closeOnBackdrop = true,
  closeOnEscape = true,
  showCloseButton = true,
  bodyClassName = 'p-4',
  className = '',
  children,
}: PopupModalProps) {
  useEffect(() => {
    if (!onClose || !closeOnEscape) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, closeOnEscape]);

  const maxWidthClass = MAX_WIDTH_CLASSES[maxWidth] ?? MAX_WIDTH_CLASSES.md;

  return (
    <div
      className={`fixed inset-0 bg-black/75 flex items-center justify-center ${zIndex} p-4 sm:p-6`}
      onClick={closeOnBackdrop && onClose ? onClose : undefined}
    >
      <div
        className={`bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary card-shadow shadow-2xl w-full max-w-[calc(100vw-2rem)] ${maxWidthClass} max-h-[calc(100vh-2rem)] sm:max-h-[calc(100vh-3rem)] flex flex-col overflow-hidden ${className}`.trim()}
        onClick={(e: MouseEvent) => e.stopPropagation()}
      >
        {(title || icon || headerActions || (showCloseButton && onClose)) && (
          <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between flex-wrap gap-3 flex-shrink-0">
            <div className="flex items-center gap-2.5 min-w-0">
              {icon && <div className="flex-shrink-0">{icon}</div>}
              <div className="min-w-0">
                {title && (
                  <h2 className="text-lg font-semibold text-white flex items-center gap-2 flex-wrap">
                    {title}
                  </h2>
                )}
                {subtitle && <div className="text-xs text-bambu-gray mt-0.5">{subtitle}</div>}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap flex-shrink-0">
              {headerActions}
              {showCloseButton && onClose && (
                <button
                  type="button"
                  onClick={onClose}
                  aria-label="Close modal"
                  className="p-1.5 rounded-lg hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              )}
            </div>
          </div>
        )}

        {subHeader && <div className="flex-shrink-0">{subHeader}</div>}

        <div className={`overflow-y-auto flex-1 min-h-0 ${bodyClassName}`.trim()}>
          {children}
        </div>

        {footer && (
          <div className="p-4 border-t border-bambu-dark-tertiary flex-shrink-0">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
