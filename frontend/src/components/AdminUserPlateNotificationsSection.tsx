import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Bell, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import type { UserEmailPreferences } from '../api/client';
import { useToast } from '../contexts/ToastContext';

interface AdminUserPlateNotificationsSectionProps {
  userId: number;
}

export function AdminUserPlateNotificationsSection({ userId }: AdminUserPlateNotificationsSectionProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const { data: prefs, isLoading } = useQuery({
    queryKey: ['admin-user-email-preferences', userId],
    queryFn: () => api.getAdminUserEmailPreferences(userId),
    enabled: Boolean(userId),
  });

  const updateMutation = useMutation({
    mutationFn: (next: UserEmailPreferences) => api.updateAdminUserEmailPreferences(userId, next),
    onSuccess: (updated) => {
      queryClient.setQueryData(['admin-user-email-preferences', userId], updated);
      showToast(t('notifications.userEmail.saveSuccess', 'Email notification preferences updated'), 'success');
    },
    onError: (err: Error) => {
      showToast(err.message || t('notifications.userEmail.saveError', 'Failed to update preferences'), 'error');
    },
  });

  if (isLoading || !prefs) {
    return (
      <div className="p-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg flex items-center gap-2 text-xs text-bambu-gray">
        <Loader2 className="w-4 h-4 animate-spin text-bambu-green" />
        <span>{t('common.loading', 'Loading...')}</span>
      </div>
    );
  }

  const toggleField = (field: 'notify_plate_clear_required' | 'notify_plate_not_empty') => {
    const current = prefs[field] ?? true;
    updateMutation.mutate({
      ...prefs,
      [field]: !current,
    });
  };

  return (
    <div className="p-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg space-y-2.5">
      <div className="flex items-center gap-2">
        <Bell className="w-4 h-4 text-bambu-green" />
        <span className="text-sm font-medium text-white">
          {t('notifications.userEmail.plateNotifications', 'Build Plate Notifications (Admin Only)')}
        </span>
      </div>
      <p className="text-xs text-bambu-gray">
        {t(
          'notifications.userEmail.adminPlateControlDesc',
          'Only administrators can disable build plate clear notifications for a user.'
        )}
      </p>

      <div className="space-y-2 pt-1">
        <label className="flex items-center justify-between gap-3 cursor-pointer">
          <span className="text-xs text-white">
            {t('notifications.userEmail.plateClearRequired', 'Plate Clear Required Email')}
          </span>
          <input
            type="checkbox"
            checked={prefs.notify_plate_clear_required ?? true}
            disabled={updateMutation.isPending}
            onChange={() => toggleField('notify_plate_clear_required')}
            className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green bg-bambu-dark"
          />
        </label>

        <label className="flex items-center justify-between gap-3 cursor-pointer">
          <span className="text-xs text-white">
            {t('notifications.userEmail.plateNotEmpty', 'Objects Detected on Plate Email')}
          </span>
          <input
            type="checkbox"
            checked={prefs.notify_plate_not_empty ?? true}
            disabled={updateMutation.isPending}
            onChange={() => toggleField('notify_plate_not_empty')}
            className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green bg-bambu-dark"
          />
        </label>
      </div>
    </div>
  );
}
