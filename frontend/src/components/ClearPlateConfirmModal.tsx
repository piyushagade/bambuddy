import { useEffect, useState, useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Camera,
  CheckCircle2,
  FileText,
  Lightbulb,
  Loader2,
  RefreshCw,
  User as UserIcon,
  VideoOff,
  WifiOff,
  X,
} from 'lucide-react';
import { api, withStreamToken } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { PlateClearedIcon } from './icons/PlateClearedIcon';

export interface ClearPlateConfirmModalProps {
  printerId: number;
  printerName: string;
  connected?: boolean;
  chamberLightOn?: boolean;
  canControlLight?: boolean;
  canViewCamera?: boolean;
  lastPrintName?: string | null;
  lastPrintUser?: string | null;
  isSubmitting?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ClearPlateConfirmModal({
  printerId,
  printerName,
  connected = false,
  chamberLightOn,
  canControlLight = false,
  canViewCamera = true,
  lastPrintName,
  lastPrintUser,
  isSubmitting = false,
  onConfirm,
  onCancel,
}: ClearPlateConfirmModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const [preparingLight, setPreparingLight] = useState<boolean>(
    Boolean(connected && chamberLightOn === false && canControlLight),
  );
  const [lightTurnedOn, setLightTurnedOn] = useState<boolean>(Boolean(chamberLightOn));
  const [snapshotBust, setSnapshotBust] = useState<number>(() => Date.now());
  const [imageLoading, setImageLoading] = useState<boolean>(Boolean(connected && canViewCamera));
  const [imageError, setImageError] = useState<boolean>(false);
  const didInitRef = useRef(false);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isSubmitting) {
        onCancel();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onCancel, isSubmitting]);

  // Turn on chamber light before taking the picture if needed
  useEffect(() => {
    if (didInitRef.current) return;
    didInitRef.current = true;

    let cancelled = false;
    const initSnapshot = async () => {
      if (!connected || !canViewCamera) {
        setPreparingLight(false);
        setImageLoading(false);
        return;
      }

      if (chamberLightOn === false && canControlLight) {
        setPreparingLight(true);
        setImageLoading(true);
        setImageError(false);
        try {
          await api.setChamberLight(printerId, true);
          if (!cancelled) {
            setLightTurnedOn(true);
            queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
          }
          // Wait briefly for the chamber LED to illuminate and camera auto-exposure to adjust
          await new Promise((resolve) => setTimeout(resolve, 1800));
        } catch {
          // Even if turning on the light fails, still proceed to capture snapshot
        }
      }

      if (!cancelled) {
        setPreparingLight(false);
        setImageError(false);
        setImageLoading(true);
        setSnapshotBust(Date.now());
      }
    };

    void initSnapshot();
    return () => {
      cancelled = true;
    };
  }, [connected, canViewCamera, chamberLightOn, canControlLight, printerId, queryClient]);

  const handleRefreshSnapshot = useCallback(async () => {
    if (!connected || !canViewCamera) return;
    setImageError(false);
    setImageLoading(true);
    if (!lightTurnedOn && chamberLightOn === false && canControlLight) {
      setPreparingLight(true);
      try {
        await api.setChamberLight(printerId, true);
        setLightTurnedOn(true);
        queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
        await new Promise((resolve) => setTimeout(resolve, 1500));
      } catch {
        // Ignore error and still refresh snapshot
      } finally {
        setPreparingLight(false);
      }
    }
    setSnapshotBust(Date.now());
  }, [connected, canViewCamera, lightTurnedOn, chamberLightOn, canControlLight, printerId, queryClient]);

  const snapshotUrl =
    connected && canViewCamera && !preparingLight
      ? withStreamToken(`/api/v1/printers/${printerId}/camera/snapshot?ensure_light=true&t=${snapshotBust}`)
      : null;

  return (
    <div
      className="fixed inset-0 bg-black/75 flex items-center justify-center z-50 p-4"
      onClick={() => {
        if (!isSubmitting) onCancel();
      }}
    >
      <Card
        className="w-full max-w-lg border border-bambu-dark-tertiary overflow-hidden shadow-2xl"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-bambu-dark-tertiary bg-bambu-dark-secondary">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="p-2 rounded-lg bg-yellow-500/15 border border-yellow-500/30 text-yellow-400 shrink-0">
              <PlateClearedIcon className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-white truncate">
                {t('printers.plateStatus.confirmModalTitle', 'Confirm Build Plate is Clear')}
              </h3>
              <p className="text-xs text-bambu-gray truncate">{printerName}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="p-1.5 rounded-lg text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary transition-colors disabled:opacity-50"
            aria-label={t('common.close', 'Close')}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <CardContent className="p-5 space-y-4">
          {/* Last Print & Owner Info */}
          <div className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary space-y-1.5">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="text-bambu-gray flex items-center gap-1.5 shrink-0">
                <UserIcon className="w-3.5 h-3.5 text-yellow-400" />
                {t('printers.plateStatus.lastPrintOwner', 'Last print belongs to:')}
              </span>
              <span className="font-semibold text-white truncate">
                {lastPrintUser || t('printers.plateStatus.unknownUser', 'Unknown user')}
              </span>
            </div>
            {lastPrintName && (
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="text-bambu-gray flex items-center gap-1.5 shrink-0">
                  <FileText className="w-3.5 h-3.5 text-bambu-gray" />
                  {t('printers.plateStatus.lastPrintJob', 'Last print job:')}
                </span>
                <span className="text-gray-200 truncate font-medium" title={lastPrintName}>
                  {lastPrintName}
                </span>
              </div>
            )}
          </div>

          {/* Camera Snapshot Section */}
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs font-medium text-bambu-gray">
                <Camera className="w-3.5 h-3.5 text-bambu-green" />
                <span>{t('printers.plateStatus.latestSnapshot', 'Latest Camera Snapshot')}</span>
                {(lightTurnedOn || chamberLightOn) && connected && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-yellow-500/15 text-yellow-400 border border-yellow-500/30 text-[10px]">
                    <Lightbulb className="w-3 h-3" />
                    {t('printers.plateStatus.lightOn', 'Chamber Light On')}
                  </span>
                )}
              </div>
              {connected && canViewCamera && (
                <button
                  type="button"
                  onClick={() => void handleRefreshSnapshot()}
                  disabled={preparingLight || imageLoading}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-bambu-gray hover:text-white bg-bambu-dark border border-bambu-dark-tertiary hover:border-bambu-gray/50 transition-colors disabled:opacity-50"
                >
                  <RefreshCw className={`w-3 h-3 ${preparingLight || imageLoading ? 'animate-spin' : ''}`} />
                  {t('common.refresh', 'Refresh')}
                </button>
              )}
            </div>

            <div className="relative aspect-video w-full rounded-lg overflow-hidden bg-black border border-bambu-dark-tertiary flex items-center justify-center">
              {!connected ? (
                <div className="flex flex-col items-center justify-center text-center p-4 text-bambu-gray">
                  <WifiOff className="w-7 h-7 mb-2 opacity-60" />
                  <p className="text-xs font-medium text-white">
                    {t('printers.plateStatus.offlineNoSnapshot', 'Printer is offline — camera snapshot unavailable')}
                  </p>
                  <p className="text-[11px] text-bambu-gray mt-0.5">
                    {t('printers.plateStatus.verifyPhysically', 'Please visually inspect the build plate at the printer.')}
                  </p>
                </div>
              ) : !canViewCamera ? (
                <div className="flex flex-col items-center justify-center text-center p-4 text-bambu-gray">
                  <VideoOff className="w-7 h-7 mb-2 opacity-60" />
                  <p className="text-xs">
                    {t('printers.plateStatus.noCameraPermission', 'Camera view permission is not enabled for your account.')}
                  </p>
                </div>
              ) : (
                <>
                  {snapshotUrl && !imageError && (
                    <img
                      key={snapshotBust}
                      src={snapshotUrl}
                      alt={`${printerName} build plate snapshot`}
                      className={`w-full h-full object-contain transition-opacity duration-200 ${
                        imageLoading ? 'opacity-0' : 'opacity-100'
                      }`}
                      onLoad={() => {
                        setImageLoading(false);
                        setImageError(false);
                      }}
                      onError={() => {
                        setImageLoading(false);
                        setImageError(true);
                      }}
                    />
                  )}

                  {(preparingLight || imageLoading) && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/75 text-center p-4">
                      <Loader2 className="w-6 h-6 text-bambu-green animate-spin mb-2" />
                      <p className="text-xs font-medium text-white">
                        {preparingLight
                          ? t(
                              'printers.plateStatus.turningOnLight',
                              'Turning on chamber light before capturing snapshot...',
                            )
                          : t('printers.plateStatus.capturingSnapshot', 'Capturing latest camera snapshot...')}
                      </p>
                    </div>
                  )}

                  {!preparingLight && !imageLoading && imageError && (
                    <div className="flex flex-col items-center justify-center text-center p-4 text-bambu-gray">
                      <VideoOff className="w-7 h-7 mb-2 opacity-60" />
                      <p className="text-xs font-medium text-white">
                        {t('printers.plateStatus.snapshotUnavailable', 'Could not capture camera snapshot')}
                      </p>
                      <p className="text-[11px] text-bambu-gray mt-0.5 mb-2">
                        {t('printers.plateStatus.verifyPhysically', 'Please visually inspect the build plate at the printer.')}
                      </p>
                      <button
                        type="button"
                        onClick={() => void handleRefreshSnapshot()}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs bg-bambu-dark-secondary border border-bambu-dark-tertiary text-white hover:border-bambu-green/50 transition-colors"
                      >
                        <RefreshCw className="w-3 h-3" />
                        {t('common.retry', 'Retry')}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          <p className="text-xs text-bambu-gray leading-relaxed">
            {t(
              'printers.plateStatus.confirmDescription',
              'Verify that the previous print has been removed from the build plate. Marking the plate as cleared will allow new prints to start on this printer.',
            )}
          </p>

          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" onClick={onCancel} disabled={isSubmitting}>
              {t('common.cancel', 'Cancel')}
            </Button>
            <Button onClick={onConfirm} disabled={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
                  {t('common.saving', 'Saving...')}
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-4 h-4 mr-1.5" />
                  {t('printers.plateStatus.confirmClearedButton', 'Confirm Plate is Clear')}
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
