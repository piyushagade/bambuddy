import { Calendar, Clock, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { formatDate, parseUTCDate } from '../utils/date';

function toLocalInputString(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

export function isoToDatetimeLocal(isoStr?: string | null): string {
  const parsed = parseUTCDate(isoStr);
  if (!parsed || Number.isNaN(parsed.getTime())) return '';
  return toLocalInputString(parsed);
}

export function datetimeLocalToIso(localStr?: string | null): string | null {
  if (!localStr || !localStr.trim()) return null;
  const date = new Date(localStr);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function nextAcademicDate(monthIndex: number, day: number): string {
  const now = new Date();
  let year = now.getFullYear();
  const candidate = new Date(year, monthIndex, day, 23, 59, 0, 0);
  if (candidate.getTime() <= now.getTime()) {
    year += 1;
  }
  return toLocalInputString(new Date(year, monthIndex, day, 23, 59, 0, 0));
}

function addDaysEndOfDay(days: number): string {
  const target = new Date();
  target.setDate(target.getDate() + days);
  target.setHours(23, 59, 0, 0);
  return toLocalInputString(target);
}

interface UserExpirationFieldProps {
  value?: string | null;
  onChange: (value: string) => void;
}

export function UserExpirationField({ value = '', onChange }: UserExpirationFieldProps) {
  const { t } = useTranslation();

  const presets = [
    {
      label: t('users.form.presetFall', 'End of Fall (Dec 15)'),
      getValue: () => nextAcademicDate(11, 15),
    },
    {
      label: t('users.form.presetSpring', 'End of Spring (May 5)'),
      getValue: () => nextAcademicDate(4, 5),
    },
    {
      label: t('users.form.presetSummer', 'End of Summer (Aug 10)'),
      getValue: () => nextAcademicDate(7, 10),
    },
    {
      label: t('users.form.preset16Weeks', '+16 Weeks'),
      getValue: () => addDaysEndOfDay(112),
    },
    {
      label: t('users.form.preset1Year', '+1 Year'),
      getValue: () => addDaysEndOfDay(365),
    },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="block text-sm font-medium text-white flex items-center gap-1.5">
          <Calendar className="w-4 h-4 text-bambu-green" />
          {t('users.form.expiresAt', 'Account Termination Date')}{' '}
          <span className="text-bambu-gray font-normal">({t('users.form.optional', 'optional')})</span>
        </label>
        {value && (
          <button
            type="button"
            onClick={() => onChange('')}
            className="text-xs text-bambu-gray hover:text-white flex items-center gap-1 transition-colors"
          >
            <X className="w-3.5 h-3.5" />
            {t('users.form.neverExpires', 'Never expires')}
          </button>
        )}
      </div>
      <input
        type="datetime-local"
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        aria-label={t('users.form.expiresAt', 'Account Termination Date')}
        className="w-full px-4 py-2.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
      />
      <div className="flex flex-wrap gap-1.5 mt-2">
        {presets.map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() => onChange(preset.getValue())}
            className="px-2 py-1 text-xs rounded bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary border border-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
          >
            {preset.label}
          </button>
        ))}
      </div>
      <p className="text-xs text-bambu-gray mt-1">
        {t(
          'users.form.expiresAtHint',
          'Access is automatically revoked and pending queue jobs are cancelled when this date is reached.'
        )}
      </p>
    </div>
  );
}

export function UserExpirationBadge({
  user,
}: {
  user: { expires_at?: string | null; is_expired?: boolean };
}) {
  const { t } = useTranslation();
  if (!user.expires_at) return null;

  const expDate = parseUTCDate(user.expires_at);
  if (!expDate) return null;

  const now = new Date();
  const isExpired = Boolean(user.is_expired) || expDate.getTime() <= now.getTime();
  const daysRemaining = (expDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
  const isExpiringSoon = !isExpired && daysRemaining <= 7;
  const formatted = formatDate(user.expires_at, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  if (isExpired) {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400"
        title={formatted}
      >
        <Clock className="w-3 h-3" />
        {t('users.expiredOn', { date: formatted, defaultValue: `Expired ${formatted}` })}
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
        isExpiringSoon
          ? 'bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-300'
          : 'bg-bambu-dark-tertiary text-bambu-gray'
      }`}
      title={formatted}
    >
      <Clock className="w-3 h-3" />
      {t('users.expiresOn', { date: formatted, defaultValue: `Expires ${formatted}` })}
    </span>
  );
}
