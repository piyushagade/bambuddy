import { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Bell, CheckCircle2, Lock, Loader2, Mail, Save } from 'lucide-react';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Button } from '../components/Button';
import { Card, CardContent, CardHeader } from '../components/Card';

export function NotificationsPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [notifyPrintStart, setNotifyPrintStart] = useState(true);
  const [notifyPrintComplete, setNotifyPrintComplete] = useState(true);
  const [notifyPrintFailed, setNotifyPrintFailed] = useState(true);
  const [notifyPrintStopped, setNotifyPrintStopped] = useState(true);
  const [notifyPrintProgress, setNotifyPrintProgress] = useState(true);
  const [notifyFirstLayerComplete, setNotifyFirstLayerComplete] = useState(true);
  const [notifyPrintConfirmRequest, setNotifyPrintConfirmRequest] = useState(true);
  const [notifyPlateClearRequired, setNotifyPlateClearRequired] = useState(true);
  const [notifyPlateNotEmpty, setNotifyPlateNotEmpty] = useState(true);
  const [notifyQueueWaiting, setNotifyQueueWaiting] = useState(true);
  const [notifyQueueSkipped, setNotifyQueueSkipped] = useState(true);
  const [notifyQueueFailed, setNotifyQueueFailed] = useState(true);
  const [isDirty, setIsDirty] = useState(false);

  // Check email gateway / advanced auth status - redirect if neither is configured
  const { data: advancedAuthStatus, isLoading: isAdvancedAuthLoading } = useQuery({
    queryKey: ['advancedAuthStatus'],
    queryFn: api.getAdvancedAuthStatus,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  const { data: settings, isLoading: isSettingsLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    staleTime: 5 * 60 * 1000,
  });

  // Fetch current preferences
  const { data: preferences, isLoading } = useQuery({
    queryKey: ['user-email-preferences'],
    queryFn: () => api.getUserEmailPreferences(),
  });

  // Redirect to settings if neither Email Gateway nor Advanced Auth is enabled
  useEffect(() => {
    if (
      (advancedAuthStatus && !advancedAuthStatus.advanced_auth_enabled && !advancedAuthStatus.smtp_configured) ||
      (settings && !settings.user_notifications_enabled)
    ) {
      navigate('/settings', { replace: true });
    }
  }, [advancedAuthStatus, settings, navigate]);

  // Populate form when preferences load
  useEffect(() => {
    if (preferences) {
      setNotifyPrintStart(preferences.notify_print_start);
      setNotifyPrintComplete(preferences.notify_print_complete);
      setNotifyPrintFailed(preferences.notify_print_failed);
      setNotifyPrintStopped(preferences.notify_print_stopped);
      setNotifyPrintProgress(preferences.notify_print_progress ?? true);
      setNotifyFirstLayerComplete(preferences.notify_first_layer_complete ?? true);
      setNotifyPrintConfirmRequest(preferences.notify_print_confirm_request ?? true);
      setNotifyPlateClearRequired(preferences.notify_plate_clear_required ?? true);
      setNotifyPlateNotEmpty(preferences.notify_plate_not_empty ?? true);
      setNotifyQueueWaiting(preferences.notify_queue_waiting ?? true);
      setNotifyQueueSkipped(preferences.notify_queue_skipped ?? true);
      setNotifyQueueFailed(preferences.notify_queue_failed ?? true);
      setIsDirty(false);
    }
  }, [preferences]);

  // Save preferences
  const saveMutation = useMutation({
    mutationFn: () =>
      api.updateUserEmailPreferences({
        notify_print_start: notifyPrintStart,
        notify_print_complete: notifyPrintComplete,
        notify_print_failed: notifyPrintFailed,
        notify_print_stopped: notifyPrintStopped,
        notify_print_progress: notifyPrintProgress,
        notify_first_layer_complete: notifyFirstLayerComplete,
        notify_print_confirm_request: notifyPrintConfirmRequest,
        notify_plate_clear_required: notifyPlateClearRequired,
        notify_plate_not_empty: notifyPlateNotEmpty,
        notify_queue_waiting: notifyQueueWaiting,
        notify_queue_skipped: notifyQueueSkipped,
        notify_queue_failed: notifyQueueFailed,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['user-email-preferences'] });
      setIsDirty(false);
      showToast(t('notifications.userEmail.saveSuccess'), 'success');
    },
    onError: (err: Error) => {
      showToast(err.message || t('notifications.userEmail.saveError'), 'error');
    },
  });

  const handleToggle = (
    setter: React.Dispatch<React.SetStateAction<boolean>>,
    value: boolean
  ) => {
    setter(!value);
    setIsDirty(true);
  };

  if (isLoading || isAdvancedAuthLoading || isSettingsLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      </div>
    );
  }

  const isAdmin = Boolean(user?.is_admin);

  const renderToggleRow = (
    title: string,
    description: string,
    checked: boolean,
    setter: React.Dispatch<React.SetStateAction<boolean>>,
    lockedToAdminDisable = false
  ) => {
    const isLocked = lockedToAdminDisable && !isAdmin && checked;
    return (
      <div className="flex items-center justify-between p-4 bg-bambu-dark rounded-lg">
        <div className="flex items-center gap-3 pr-4">
          <div className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 ${checked ? 'bg-bambu-green/20' : 'bg-bambu-dark-tertiary'}`}>
            <CheckCircle2 className={`w-5 h-5 ${checked ? 'text-bambu-green' : 'text-bambu-gray'}`} />
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-white font-medium">{title}</p>
              {lockedToAdminDisable && !isAdmin && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-amber-500/20 text-amber-400">
                  <Lock className="w-3 h-3" />
                  {t('notifications.userEmail.adminOnlyDisableBadge', 'Admin-managed')}
                </span>
              )}
            </div>
            <p className="text-sm text-bambu-gray">{description}</p>
          </div>
        </div>
        <button
          type="button"
          disabled={isLocked}
          onClick={() => {
            if (isLocked) return;
            handleToggle(setter, checked);
          }}
          title={
            isLocked
              ? t('notifications.userEmail.adminOnlyDisableHint', 'Only administrators can disable plate clear notifications')
              : undefined
          }
          className={`relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-bambu-green focus:ring-offset-2 focus:ring-offset-bambu-dark ${
            checked ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
          } ${isLocked ? 'opacity-60 cursor-not-allowed' : ''}`}
          role="switch"
          aria-checked={checked}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              checked ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
      </div>
    );
  };

  return (
    <div className="p-4 md:p-6 max-w-2xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Bell className="w-7 h-7 text-bambu-green" />
        <h1 className="text-2xl font-bold text-white">{t('notifications.userEmail.title')}</h1>
      </div>

      {/* Info card */}
      <Card className="mb-6 border-blue-300 bg-blue-50 dark:border-blue-500/30 dark:bg-blue-500/5">
        <CardContent className="py-4">
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-full flex items-center justify-center bg-blue-100 dark:bg-blue-500/20 flex-shrink-0">
              <Mail className="w-5 h-5 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h3 className="text-white font-medium">{t('notifications.userEmail.emailNotifications')}</h3>
              <p className="text-sm text-bambu-gray mt-1">
                {t('notifications.userEmail.emailNotificationsDesc')}
              </p>
              {user?.email ? (
                <p className="text-sm text-blue-700 dark:text-blue-400 mt-2">
                  {t('notifications.userEmail.sendingTo')}: <strong>{user.email}</strong>
                </p>
              ) : (
                <p className="text-sm text-yellow-700 dark:text-yellow-400 mt-2">
                  {t('notifications.userEmail.noEmailWarning')}
                </p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Print Job Notifications card */}
      <Card className="mb-6">
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">{t('notifications.userEmail.printJobNotifications')}</h2>
          <p className="text-sm text-bambu-gray mt-1">{t('notifications.userEmail.printJobNotificationsDesc')}</p>
        </CardHeader>
        <CardContent className="space-y-4">
          {renderToggleRow(
            t('notifications.userEmail.printJobStarts'),
            t('notifications.userEmail.printJobStartsDesc'),
            notifyPrintStart,
            setNotifyPrintStart
          )}
          {renderToggleRow(
            t('notifications.userEmail.firstLayerComplete', 'First Layer Completed'),
            t('notifications.userEmail.firstLayerCompleteDesc', 'Receive an email with a camera snapshot when the first layer finishes.'),
            notifyFirstLayerComplete,
            setNotifyFirstLayerComplete
          )}
          {renderToggleRow(
            t('notifications.userEmail.printProgress', 'Print Progress Milestones'),
            t('notifications.userEmail.printProgressDesc', 'Receive email updates when your print reaches 25%, 50%, and 75% progress.'),
            notifyPrintProgress,
            setNotifyPrintProgress
          )}
          {renderToggleRow(
            t('notifications.userEmail.printJobFinishes'),
            t('notifications.userEmail.printJobFinishesDesc'),
            notifyPrintComplete,
            setNotifyPrintComplete
          )}
          {renderToggleRow(
            t('notifications.userEmail.printConfirmRequest', 'Outcome Confirmation Request'),
            t('notifications.userEmail.printConfirmRequestDesc', 'Receive an email with one-click Good / Reject links when your print requests outcome confirmation.'),
            notifyPrintConfirmRequest,
            setNotifyPrintConfirmRequest
          )}
          {renderToggleRow(
            t('notifications.userEmail.printErrors'),
            t('notifications.userEmail.printErrorsDesc'),
            notifyPrintFailed,
            setNotifyPrintFailed
          )}
          {renderToggleRow(
            t('notifications.userEmail.printJobStops'),
            t('notifications.userEmail.printJobStopsDesc'),
            notifyPrintStopped,
            setNotifyPrintStopped
          )}
        </CardContent>
      </Card>

      {/* Build Plate Notifications card */}
      <Card className="mb-6">
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">
            {t('notifications.userEmail.plateNotifications', 'Build Plate Notifications')}
          </h2>
          <p className="text-sm text-bambu-gray mt-1">
            {t(
              'notifications.userEmail.plateNotificationsDesc',
              'Alerts when a build plate needs to be cleared or objects are detected before printing. Only administrators can turn these off.'
            )}
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {renderToggleRow(
            t('notifications.userEmail.plateClearRequired', 'Plate Clear Required'),
            t('notifications.userEmail.plateClearRequiredDesc', 'Receive an email when your print finishes and the build plate must be cleared before the next job can start.'),
            notifyPlateClearRequired,
            setNotifyPlateClearRequired,
            true
          )}
          {renderToggleRow(
            t('notifications.userEmail.plateNotEmpty', 'Objects Detected on Plate'),
            t('notifications.userEmail.plateNotEmptyDesc', 'Receive an email if objects are detected on the build plate when your print is starting.'),
            notifyPlateNotEmpty,
            setNotifyPlateNotEmpty,
            true
          )}
        </CardContent>
      </Card>

      {/* Print Queue Notifications card */}
      <Card className="mb-6">
        <CardHeader>
          <h2 className="text-lg font-semibold text-white">
            {t('notifications.userEmail.queueNotifications', 'Print Queue Notifications')}
          </h2>
          <p className="text-sm text-bambu-gray mt-1">
            {t('notifications.userEmail.queueNotificationsDesc', 'Receive email updates when your queued print jobs are waiting, skipped, or fail to start.')}
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {renderToggleRow(
            t('notifications.userEmail.queueWaiting', 'Queued Job Waiting'),
            t('notifications.userEmail.queueWaitingDesc', 'Receive an email when your queued job is waiting for filament or action.'),
            notifyQueueWaiting,
            setNotifyQueueWaiting
          )}
          {renderToggleRow(
            t('notifications.userEmail.queueSkipped', 'Queued Job Skipped'),
            t('notifications.userEmail.queueSkippedDesc', 'Receive an email when your queued job is skipped because a previous print failed.'),
            notifyQueueSkipped,
            setNotifyQueueSkipped
          )}
          {renderToggleRow(
            t('notifications.userEmail.queueFailed', 'Queued Job Failed to Start'),
            t('notifications.userEmail.queueFailedDesc', 'Receive an email if your queued job fails to upload or start on the printer.'),
            notifyQueueFailed,
            setNotifyQueueFailed
          )}
        </CardContent>
      </Card>

      {/* Save button */}
      <div className="flex justify-end">
        <Button
          onClick={() => saveMutation.mutate()}
          disabled={!isDirty || saveMutation.isPending || !user?.email}
        >
          {saveMutation.isPending ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('common.saving')}
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              {t('common.save')}
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
