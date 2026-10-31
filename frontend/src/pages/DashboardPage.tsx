import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Disc3,
  Edit2,
  FileText,
  FolderKanban,
  FolderOpen,
  Globe,
  Layers,
  LayoutDashboard,
  ListOrdered,
  Loader2,
  Lock,
  Play,
  Plus,
  Printer as PrinterIcon,
  RotateCcw,
  Search,
  Server,
  Settings,
  Shield,
  ShieldAlert,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  UserCheck,
  Users,
  Wrench,
  XCircle,
} from 'lucide-react';
import {
  api,
  ApiError,
  withMediaToken,
  type Archive,
  type InventorySpool,
  type LibraryFileListItem,
  type Printer,
  type PrinterCreate,
  type PrinterStatus,
} from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { SpoolFormModal } from '../components/SpoolFormModal';
import { AssignToAmsModal } from '../components/spoolbuddy/AssignToAmsModal';
import { PrintModal } from '../components/PrintModal';
import { AddPrinterModal, EditPrinterModal } from './PrintersPage';
import { getCurrencySymbol } from '../utils/currency';
import { parseUTCDate } from '../utils/date';

function formatDurationMinutes(minutes: number | null | undefined): string {
  if (!minutes || minutes <= 0) return '0m';
  const hrs = Math.floor(minutes / 60);
  const mins = Math.round(minutes % 60);
  if (hrs === 0) return `${mins}m`;
  if (mins === 0) return `${hrs}h`;
  return `${hrs}h ${mins}m`;
}

function formatHour(hour: number | null | undefined): string {
  if (hour === null || hour === undefined) return '';
  const normalized = ((hour % 24) + 24) % 24;
  return `${String(normalized).padStart(2, '0')}:00`;
}

function getAmsSlotLabel(amsId: number, trayId: number): string {
  if (amsId === 255 || amsId === 254 || trayId === 254 || trayId === 255) {
    return 'External';
  }
  const unitLetter =
    amsId >= 128 && amsId <= 135
      ? `HT-${String.fromCharCode(65 + amsId - 128)}`
      : amsId <= 3
        ? String.fromCharCode(65 + amsId)
        : String(amsId);
  return `AMS ${unitLetter}${trayId + 1}`;
}

export function DashboardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { user, isAdmin, authEnabled, hasPermission, hasAnyPermission } = useAuth();

  // Admin can switch between 'admin' and 'maker' view to preview or focus
  const canUseAdminDashboard =
    isAdmin ||
    hasAnyPermission(
      'users:read',
      'groups:read',
      'queue:approve',
      'printers:create',
      'settings:update',
    );
  const [dashboardMode, setDashboardMode] = useState<'admin' | 'maker'>(() => {
    const saved = localStorage.getItem('bambuddy_dashboard_mode');
    return saved === 'maker' ? 'maker' : 'admin';
  });
  const effectiveAdminMode = canUseAdminDashboard && dashboardMode === 'admin';

  const handleModeChange = (mode: 'admin' | 'maker') => {
    setDashboardMode(mode);
    localStorage.setItem('bambuddy_dashboard_mode', mode);
  };

  // Search / filter state for spools and files
  const [spoolSearch, setSpoolSearch] = useState('');
  const [spoolMaterialFilter, setSpoolMaterialFilter] = useState('all');
  const [fileSearch, setFileSearch] = useState('');

  // Modal states
  const [showAddPrinterModal, setShowAddPrinterModal] = useState(false);
  const [editingPrinter, setEditingPrinter] = useState<Printer | null>(null);
  const [spoolModalState, setSpoolModalState] = useState<{
    open: boolean;
    mode: 'create' | 'edit';
    spool: InventorySpool | null;
  }>({ open: false, mode: 'create', spool: null });
  const [assigningSpool, setAssigningSpool] = useState<InventorySpool | null>(null);
  const [printTarget, setPrintTarget] = useState<{
    libraryFileId?: number;
    archiveId?: number;
    name: string;
  } | null>(null);

  // ── Data Queries ──────────────────────────────────────────────────────────

  const { data: uiFlags } = useQuery({
    queryKey: ['ui-flags'],
    queryFn: api.getUiFlags,
    staleTime: 5 * 60 * 1000,
  });
  const currencySymbol = getCurrencySymbol(uiFlags?.currency ?? 'USD');

  const canReadPrinters = hasPermission('printers:read');
  const { data: printers = [], isLoading: printersLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
    enabled: canReadPrinters,
  });

  const printerStatusQueries = useQueries({
    queries: printers.map((printer) => ({
      queryKey: ['printerStatus', printer.id],
      queryFn: () => api.getPrinterStatus(printer.id),
      refetchInterval: 10000,
      enabled: canReadPrinters,
    })),
  });

  const statusByPrinterId = useMemo(() => {
    const map: Record<number, PrinterStatus | undefined> = {};
    printers.forEach((p, idx) => {
      map[p.id] = printerStatusQueries[idx]?.data;
    });
    return map;
  }, [printers, printerStatusQueries]);

  const canReadInventory = hasPermission('inventory:read');
  const { data: spools = [], isLoading: spoolsLoading } = useQuery({
    queryKey: ['spools', 'authorized'],
    queryFn: () => api.getSpools(false, true),
    enabled: canReadInventory,
  });

  const { data: spoolAssignments = [] } = useQuery({
    queryKey: ['spool-assignments'],
    queryFn: () => api.getAssignments(),
    enabled: canReadInventory,
  });

  const canReadProjects = hasAnyPermission('projects:read', 'projects:read_own', 'projects:read_all');
  const { data: projects = [], isLoading: projectsLoading } = useQuery({
    queryKey: ['projects', 'active'],
    queryFn: () => api.getProjects('active'),
    enabled: canReadProjects,
  });

  const canReadLibrary = hasAnyPermission('library:read', 'library:read_own', 'library:read_all');
  const { data: libraryFiles = [], isLoading: filesLoading } = useQuery({
    queryKey: ['library-files', 'dashboard'],
    queryFn: () => api.getLibraryFiles(undefined, true, undefined, undefined, true),
    enabled: canReadLibrary,
  });

  const canReadQueue = hasAnyPermission('queue:read', 'queue:read_own', 'queue:read_all');
  const { data: queueItems = [] } = useQuery({
    queryKey: ['queue'],
    queryFn: () => api.getQueue(),
    enabled: canReadQueue,
    refetchInterval: 15000,
  });

  const canReadArchives = hasAnyPermission('archives:read', 'archives:read_own', 'archives:read_all');
  const { data: recentArchives = [] } = useQuery({
    queryKey: ['archives', 'dashboard-recent'],
    queryFn: () => api.getArchives(undefined, undefined, 6, 0),
    enabled: canReadArchives,
  });

  const canReadMaintenance = hasPermission('maintenance:read');
  const { data: maintenanceOverview = [] } = useQuery({
    queryKey: ['maintenanceOverview'],
    queryFn: api.getMaintenanceOverview,
    enabled: canReadMaintenance && effectiveAdminMode,
    staleTime: 60 * 1000,
  });

  const canReadSystem = hasPermission('system:read');
  const { data: systemInfo } = useQuery({
    queryKey: ['system-info'],
    queryFn: api.getSystemInfo,
    enabled: canReadSystem && effectiveAdminMode,
    staleTime: 60 * 1000,
  });

  // ── Mutations ─────────────────────────────────────────────────────────────

  const addPrinterMutation = useMutation({
    mutationFn: (data: PrinterCreate) => api.createPrinter(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      setShowAddPrinterModal(false);
      showToast(t('printers.toast.printerAdded', 'Printer added successfully'), 'success');
    },
    onError: (error: Error) => {
      if (error instanceof ApiError && error.code === 'printer_connection_failed') {
        showToast(t('printers.toast.connectionFailedNotAdded'), 'error');
        return;
      }
      showToast(error.message || t('printers.toast.failedToAdd'), 'error');
    },
  });

  const clearPlateMutation = useMutation({
    mutationFn: (printerId: number) => api.clearPlate(printerId),
    onSuccess: (_, printerId) => {
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.plateCleared', 'Build plate marked as cleared'), 'success');
    },
    onError: (err: Error) => showToast(err.message, 'error'),
  });

  const approveQueueMutation = useMutation({
    mutationFn: (itemId: number) => api.approveQueueItem(itemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('dashboard.admin.jobApproved', 'Print job approved'), 'success');
    },
    onError: (err: Error) => showToast(err.message, 'error'),
  });

  const rejectQueueMutation = useMutation({
    mutationFn: (itemId: number) => api.rejectQueueItem(itemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast(t('dashboard.admin.jobRejected', 'Print job rejected'), 'info');
    },
    onError: (err: Error) => showToast(err.message, 'error'),
  });

  // ── Derived Metrics & Filtered Lists ──────────────────────────────────────

  // Map spool_id -> assignments
  const assignmentsBySpoolId = useMemo(() => {
    const map = new Map<number, typeof spoolAssignments>();
    for (const a of spoolAssignments) {
      const existing = map.get(a.spool_id) ?? [];
      existing.push(a);
      map.set(a.spool_id, existing);
    }
    return map;
  }, [spoolAssignments]);

  // Accessible printer IDs set
  const accessiblePrinterIds = useMemo(() => new Set(printers.map((p) => p.id)), [printers]);

  // Printer fleet stats
  const printerStats = useMemo(() => {
    let printing = 0;
    let idle = 0;
    let pausedOrError = 0;
    let offline = 0;
    let awaitingPlateClear = 0;

    for (const p of printers) {
      const st = statusByPrinterId[p.id];
      if (st?.awaiting_plate_clear) awaitingPlateClear++;
      if (!st || !st.connected) {
        offline++;
      } else if (st.state === 'RUNNING') {
        printing++;
      } else if (st.state === 'PAUSE' || (st.hms_errors && st.hms_errors.length > 0)) {
        pausedOrError++;
      } else {
        idle++;
      }
    }
    return { total: printers.length, printing, idle, pausedOrError, offline, awaitingPlateClear };
  }, [printers, statusByPrinterId]);

  // Active queue items (for current user in maker mode, or farm-wide in admin mode)
  const activeQueueItems = useMemo(() => {
    return queueItems.filter((item) => {
      const isActiveStatus = ['pending', 'pending_approval', 'printing', 'waiting_for_filament'].includes(
        item.status,
      );
      if (!isActiveStatus) return false;
      if (!effectiveAdminMode && authEnabled && user) {
        return item.created_by_id === user.id;
      }
      return true;
    });
  }, [queueItems, effectiveAdminMode, authEnabled, user]);

  // Pending approval items for Admin Action Center
  const pendingApprovalItems = useMemo(
    () => queueItems.filter((item) => item.status === 'pending_approval'),
    [queueItems],
  );

  // Printers needing attention (HMS errors, awaiting plate clear, or paused)
  const printersNeedingAttention = useMemo(() => {
    return printers.filter((p) => {
      const st = statusByPrinterId[p.id];
      if (!st) return false;
      const hasHmsError = (st.hms_errors?.length ?? 0) > 0;
      const isPaused = st.state === 'PAUSE';
      const needsClear = !!st.awaiting_plate_clear;
      return hasHmsError || isPaused || needsClear;
    });
  }, [printers, statusByPrinterId]);

  // Low stock spools
  const lowStockSpools = useMemo(() => {
    return spools.filter((s) => {
      const labelWeight = s.label_weight || 1000;
      const remaining = Math.max(0, labelWeight - (s.weight_used || 0));
      const pct = (remaining / labelWeight) * 100;
      const threshold = s.low_stock_threshold_pct ?? 15;
      return pct <= threshold;
    });
  }, [spools]);

  // Overdue maintenance items
  const maintenanceAlerts = useMemo(() => {
    return maintenanceOverview.filter((m) => m.due_count > 0 || m.warning_count > 0);
  }, [maintenanceOverview]);

  // Distinct materials in allowed spools
  const spoolMaterials = useMemo(() => {
    const set = new Set<string>();
    spools.forEach((s) => {
      if (s.material) set.add(s.material);
    });
    return Array.from(set).sort();
  }, [spools]);

  // Filtered allowed spools
  const filteredSpools = useMemo(() => {
    const q = spoolSearch.trim().toLowerCase();
    return spools.filter((s) => {
      if (spoolMaterialFilter !== 'all' && s.material !== spoolMaterialFilter) return false;
      if (!q) return true;
      const haystack = [s.brand, s.material, s.subtype, s.color_name, s.note, s.storage_location]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [spools, spoolSearch, spoolMaterialFilter]);

  // Filtered library files
  const filteredFiles = useMemo(() => {
    const q = fileSearch.trim().toLowerCase();
    const sorted = [...libraryFiles].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
    if (!q) return sorted.slice(0, 8);
    return sorted
      .filter(
        (f) =>
          f.filename.toLowerCase().includes(q) ||
          (f.print_name || '').toLowerCase().includes(q),
      )
      .slice(0, 8);
  }, [libraryFiles, fileSearch]);

  // User group quotas summary
  const quotaSummary = useMemo(() => {
    if (!authEnabled || !user) return null;
    const activeGroups = (user.groups || []).filter((g) => !g.is_expired);
    const maxDuration = activeGroups
      .map((g) => g.max_print_duration_minutes)
      .filter((v): v is number => typeof v === 'number' && v > 0);
    const maxGrams = activeGroups
      .map((g) => g.max_filament_grams_per_job)
      .filter((v): v is number => typeof v === 'number' && v > 0);
    const maxQueue = activeGroups
      .map((g) => g.max_active_queue_items)
      .filter((v): v is number => typeof v === 'number' && v > 0);
    const requiresApproval = activeGroups.some((g) => g.requires_approval);
    const hoursGroup = activeGroups.find(
      (g) => g.allowed_hours_start !== null && g.allowed_hours_start !== undefined,
    );
    const earliestExpiry = activeGroups
      .map((g) => g.expires_at)
      .filter((v): v is string => !!v)
      .sort()[0];

    const myActiveQueueCount = queueItems.filter(
      (i) =>
        i.created_by_id === user.id &&
        ['pending', 'pending_approval', 'printing', 'waiting_for_filament'].includes(i.status),
    ).length;

    return {
      activeGroups,
      maxDurationMinutes: maxDuration.length > 0 ? Math.max(...maxDuration) : null,
      maxFilamentGrams: maxGrams.length > 0 ? Math.max(...maxGrams) : null,
      maxActiveQueueItems: maxQueue.length > 0 ? Math.max(...maxQueue) : null,
      myActiveQueueCount,
      requiresApproval,
      allowedHours:
        hoursGroup && hoursGroup.allowed_hours_start != null && hoursGroup.allowed_hours_end != null
          ? `${formatHour(hoursGroup.allowed_hours_start)} – ${formatHour(hoursGroup.allowed_hours_end)}`
          : null,
      earliestExpiry: earliestExpiry || user.expires_at || null,
    };
  }, [authEnabled, user, queueItems]);

  const greetingName = user?.first_name || user?.username || t('dashboard.maker', 'Maker');

  return (
    <div className="p-4 md:p-8 space-y-6 max-w-[1600px] mx-auto">
      {/* ── Top Header & Mode Switcher ──────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5 flex-wrap">
            <LayoutDashboard className="w-7 h-7 text-bambu-green" />
            <h1 className="text-2xl font-bold text-white">
              {effectiveAdminMode
                ? t('dashboard.adminTitle', 'Admin Command Center')
                : t('dashboard.makerTitle', 'Maker Control Center')}
            </h1>
            <span
              className={`text-xs font-medium px-2.5 py-0.5 rounded-full border ${
                effectiveAdminMode
                  ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                  : 'bg-bambu-green/15 text-bambu-green border-bambu-green/30'
              }`}
            >
              {effectiveAdminMode
                ? t('dashboard.roleAdmin', 'Administrator')
                : t('dashboard.roleMaker', 'Maker')}
            </span>
          </div>
          <p className="text-sm text-bambu-gray mt-1">
            {t('dashboard.welcomeSubtitle', 'Welcome back, {{name}} — manage your printers, projects, files, and spools in one place.', {
              name: greetingName,
            })}
          </p>
        </div>

        {/* Admin / Maker View Switcher */}
        {canUseAdminDashboard && (
          <div className="inline-flex items-center rounded-xl bg-bambu-dark-secondary border border-bambu-dark-tertiary p-1 self-start sm:self-auto">
            <button
              type="button"
              onClick={() => handleModeChange('admin')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-colors ${
                effectiveAdminMode
                  ? 'bg-bambu-green text-black shadow-sm'
                  : 'text-bambu-gray hover:text-white'
              }`}
            >
              <Shield className="w-3.5 h-3.5" />
              {t('dashboard.viewMode.admin', 'Admin Dashboard')}
            </button>
            <button
              type="button"
              onClick={() => handleModeChange('maker')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-colors ${
                !effectiveAdminMode
                  ? 'bg-bambu-green text-black shadow-sm'
                  : 'text-bambu-gray hover:text-white'
              }`}
            >
              <Sparkles className="w-3.5 h-3.5" />
              {t('dashboard.viewMode.maker', 'Maker Dashboard')}
            </button>
          </div>
        )}
      </div>

      {/* ── KPI Summary Strip ───────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3.5">
        <Card
          onClick={() => navigate('/printers')}
          className="border-bambu-dark-tertiary hover:border-bambu-green/50 transition-colors"
        >
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-bambu-gray uppercase tracking-wider">
                {t('dashboard.kpi.accessiblePrinters', 'Accessible Printers')}
              </p>
              <div className="flex items-baseline gap-2 mt-1">
                <span className="text-2xl font-bold text-white">{printerStats.total}</span>
                <span className="text-xs text-bambu-green">
                  {t('dashboard.kpi.printingIdle', '{{printing}} printing · {{idle}} idle', {
                    printing: printerStats.printing,
                    idle: printerStats.idle,
                  })}
                </span>
              </div>
            </div>
            <div className="p-2.5 rounded-xl bg-bambu-green/15 text-bambu-green">
              <PrinterIcon className="w-5 h-5" />
            </div>
          </CardContent>
        </Card>

        <Card
          onClick={() => navigate('/projects')}
          className="border-bambu-dark-tertiary hover:border-bambu-green/50 transition-colors"
        >
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-bambu-gray uppercase tracking-wider">
                {t('dashboard.kpi.projectsAndFiles', 'Projects & Files')}
              </p>
              <div className="flex items-baseline gap-2 mt-1">
                <span className="text-2xl font-bold text-white">{projects.length}</span>
                <span className="text-xs text-blue-400">
                  {t('dashboard.kpi.filesCount', '{{count}} library files', {
                    count: libraryFiles.length,
                  })}
                </span>
              </div>
            </div>
            <div className="p-2.5 rounded-xl bg-blue-500/15 text-blue-400">
              <FolderKanban className="w-5 h-5" />
            </div>
          </CardContent>
        </Card>

        <Card
          onClick={() => navigate('/inventory')}
          className="border-bambu-dark-tertiary hover:border-bambu-green/50 transition-colors"
        >
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-bambu-gray uppercase tracking-wider">
                {t('dashboard.kpi.allowedSpools', 'Allowed Spools')}
              </p>
              <div className="flex items-baseline gap-2 mt-1">
                <span className="text-2xl font-bold text-white">{spools.length}</span>
                <span className="text-xs text-purple-400">
                  {t('dashboard.kpi.materialsCount', '{{count}} materials', {
                    count: spoolMaterials.length,
                  })}
                </span>
              </div>
            </div>
            <div className="p-2.5 rounded-xl bg-purple-500/15 text-purple-400">
              <Disc3 className="w-5 h-5" />
            </div>
          </CardContent>
        </Card>

        <Card
          onClick={() => navigate('/queue')}
          className="border-bambu-dark-tertiary hover:border-bambu-green/50 transition-colors"
        >
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-bambu-gray uppercase tracking-wider">
                {effectiveAdminMode
                  ? t('dashboard.kpi.farmQueue', 'Active Farm Queue')
                  : t('dashboard.kpi.myQueue', 'My Active Jobs')}
              </p>
              <div className="flex items-baseline gap-2 mt-1">
                <span className="text-2xl font-bold text-white">{activeQueueItems.length}</span>
                {pendingApprovalItems.length > 0 && effectiveAdminMode ? (
                  <span className="text-xs text-amber-400">
                    {t('dashboard.kpi.awaitingApproval', '{{count}} awaiting approval', {
                      count: pendingApprovalItems.length,
                    })}
                  </span>
                ) : (
                  <span className="text-xs text-bambu-gray">
                    {t('dashboard.kpi.recentPrintsCount', '{{count}} recent prints', {
                      count: recentArchives.length,
                    })}
                  </span>
                )}
              </div>
            </div>
            <div className="p-2.5 rounded-xl bg-amber-500/15 text-amber-400">
              <ListOrdered className="w-5 h-5" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Maker Quotas / Access & Certification Banner ───────────────────── */}
      {quotaSummary && (quotaSummary.activeGroups.length > 0 || user?.require_safety_certification) && (
        <div className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl p-3.5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="text-xs font-semibold text-bambu-gray uppercase tracking-wider flex items-center gap-1.5">
              <UserCheck className="w-4 h-4 text-bambu-green" />
              {t('dashboard.quotas.yourAccess', 'Your Access & Quotas:')}
            </span>
            {quotaSummary.activeGroups.map((g) => (
              <span
                key={g.id}
                className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-bambu-green/15 text-bambu-green border border-bambu-green/30"
              >
                {g.name}
              </span>
            ))}
            {user?.require_safety_certification && (
              <span
                className={`px-2.5 py-0.5 rounded-full text-xs font-medium flex items-center gap-1 border ${
                  user.is_certification_expired
                    ? 'bg-red-500/15 text-red-400 border-red-500/30'
                    : 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                }`}
              >
                {user.is_certification_expired ? (
                  <>
                    <ShieldAlert className="w-3.5 h-3.5" />
                    {t('dashboard.quotas.certExpired', 'Safety Certification Expired')}
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    {t('dashboard.quotas.certValid', 'Safety Certified')}
                  </>
                )}
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-4 text-xs text-bambu-gray">
            {quotaSummary.maxActiveQueueItems !== null && (
              <span>
                {t('dashboard.quotas.queueSlots', 'Queue Slots:')}{' '}
                <strong className="text-white">
                  {quotaSummary.myActiveQueueCount} / {quotaSummary.maxActiveQueueItems}
                </strong>
              </span>
            )}
            {quotaSummary.maxDurationMinutes !== null && (
              <span>
                {t('dashboard.quotas.maxPrintTime', 'Max Print Time:')}{' '}
                <strong className="text-white">
                  {formatDurationMinutes(quotaSummary.maxDurationMinutes)}
                </strong>
              </span>
            )}
            {quotaSummary.maxFilamentGrams !== null && (
              <span>
                {t('dashboard.quotas.maxFilament', 'Max Filament/Job:')}{' '}
                <strong className="text-white">{quotaSummary.maxFilamentGrams}g</strong>
              </span>
            )}
            {quotaSummary.allowedHours && (
              <span>
                {t('dashboard.quotas.allowedHours', 'Hours:')}{' '}
                <strong className="text-white">{quotaSummary.allowedHours}</strong>
              </span>
            )}
            {quotaSummary.requiresApproval && !isAdmin && (
              <span className="text-amber-400 font-medium">
                {t('dashboard.quotas.requiresApproval', 'Jobs require TA/Admin approval')}
              </span>
            )}
            {quotaSummary.earliestExpiry && (
              <span>
                {t('dashboard.quotas.accessExpires', 'Access Until:')}{' '}
                <strong className="text-white">
                  {parseUTCDate(quotaSummary.earliestExpiry)?.toLocaleDateString() ??
                    quotaSummary.earliestExpiry}
                </strong>
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Admin Tools & Action Center (Admin Mode Only) ──────────────────── */}
      {effectiveAdminMode && (
        <div className="space-y-4">
          {/* Quick Admin Tools Bar */}
          <Card className="border-amber-500/30 bg-gradient-to-r from-bambu-dark-secondary via-bambu-dark-secondary to-amber-500/5">
            <CardContent className="p-4 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-amber-500/15 text-amber-400">
                  <Shield className="w-5 h-5" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-white">
                    {t('dashboard.admin.quickToolsTitle', 'Admin Tools & Farm Administration')}
                  </h2>
                  <p className="text-xs text-bambu-gray">
                    {t(
                      'dashboard.admin.quickToolsSubtitle',
                      'Manage printer access restrictions, user groups, spool inventory, and farm health.',
                    )}
                  </p>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {hasPermission('printers:create') && (
                  <Button
                    size="sm"
                    onClick={() => setShowAddPrinterModal(true)}
                  >
                    <Plus className="w-3.5 h-3.5" />
                    {t('printers.addPrinter', 'Add Printer')}
                  </Button>
                )}
                {hasPermission('inventory:create') && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setSpoolModalState({ open: true, mode: 'create', spool: null })}
                  >
                    <Disc3 className="w-3.5 h-3.5 text-purple-400" />
                    {t('dashboard.admin.addSpool', 'Add Spool')}
                  </Button>
                )}
                {hasAnyPermission('users:read', 'groups:read') && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => navigate('/settings?tab=users')}
                  >
                    <Users className="w-3.5 h-3.5 text-blue-400" />
                    {t('dashboard.admin.manageUsersGroups', 'Users & Groups')}
                  </Button>
                )}
                {hasPermission('maintenance:read') && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => navigate('/maintenance')}
                  >
                    <Wrench className="w-3.5 h-3.5 text-amber-400" />
                    {t('nav.maintenance', 'Maintenance')}
                    {maintenanceAlerts.length > 0 && (
                      <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-amber-500/20 text-amber-300">
                        {maintenanceAlerts.length}
                      </span>
                    )}
                  </Button>
                )}
                {hasPermission('settings:read') && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => navigate('/settings')}
                  >
                    <Settings className="w-3.5 h-3.5" />
                    {t('nav.settings', 'Settings')}
                  </Button>
                )}
                {hasPermission('system:read') && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => navigate('/system')}
                  >
                    <Server className="w-3.5 h-3.5 text-emerald-400" />
                    {systemInfo?.storage?.disk_percent_used != null
                      ? `${t('nav.system', 'System')} (${Math.round(systemInfo.storage.disk_percent_used)}% disk)`
                      : t('nav.system', 'System')}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Admin Action Center (Pending Approvals, Printer Alerts, Low Stock) */}
          {(pendingApprovalItems.length > 0 ||
            printersNeedingAttention.length > 0 ||
            lowStockSpools.length > 0) && (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              {/* Pending Job Approvals */}
              <Card className="border-bambu-dark-tertiary">
                <CardContent className="p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                      <Clock className="w-4 h-4 text-amber-400" />
                      {t('dashboard.admin.pendingApprovals', 'Pending Job Approvals')}
                    </h3>
                    <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 font-medium">
                      {pendingApprovalItems.length}
                    </span>
                  </div>
                  {pendingApprovalItems.length === 0 ? (
                    <p className="text-xs text-bambu-gray py-3">
                      {t('dashboard.admin.noPendingApprovals', 'No print jobs waiting for approval.')}
                    </p>
                  ) : (
                    <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                      {pendingApprovalItems.map((item) => (
                        <div
                          key={item.id}
                          className="p-2.5 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary flex items-center justify-between gap-2"
                        >
                          <div className="min-w-0">
                            <p className="text-xs font-medium text-white truncate">
                              {item.archive_name || item.library_file_name || `Job #${item.id}`}
                            </p>
                            <p className="text-[11px] text-bambu-gray truncate">
                              {item.created_by_username
                                ? `By ${item.created_by_username}`
                                : 'Submitted job'}{' '}
                              {item.printer_name ? `· ${item.printer_name}` : ''}
                            </p>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              type="button"
                              onClick={() => approveQueueMutation.mutate(item.id)}
                              disabled={approveQueueMutation.isPending}
                              className="p-1.5 rounded-lg bg-bambu-green/15 text-bambu-green hover:bg-bambu-green hover:text-black transition-colors"
                              title={t('dashboard.admin.approveJob', 'Approve')}
                            >
                              <ThumbsUp className="w-3.5 h-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => rejectQueueMutation.mutate(item.id)}
                              disabled={rejectQueueMutation.isPending}
                              className="p-1.5 rounded-lg bg-red-500/15 text-red-400 hover:bg-red-500 hover:text-white transition-colors"
                              title={t('dashboard.admin.rejectJob', 'Reject')}
                            >
                              <ThumbsDown className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Printers Needing Attention */}
              <Card className="border-bambu-dark-tertiary">
                <CardContent className="p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4 text-red-400" />
                      {t('dashboard.admin.printersAttention', 'Printers Needing Attention')}
                    </h3>
                    <span className="text-xs px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 font-medium">
                      {printersNeedingAttention.length}
                    </span>
                  </div>
                  {printersNeedingAttention.length === 0 ? (
                    <p className="text-xs text-bambu-gray py-3">
                      {t('dashboard.admin.allPrintersHealthy', 'All connected printers are healthy.')}
                    </p>
                  ) : (
                    <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                      {printersNeedingAttention.map((p) => {
                        const st = statusByPrinterId[p.id];
                        const hmsCount = st?.hms_errors?.length ?? 0;
                        return (
                          <div
                            key={p.id}
                            className="p-2.5 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary flex items-center justify-between gap-2"
                          >
                            <div className="min-w-0">
                              <p className="text-xs font-medium text-white truncate">{p.name}</p>
                              <p className="text-[11px] text-amber-400 truncate">
                                {st?.awaiting_plate_clear
                                  ? t('dashboard.printers.awaitingPlateClear', 'Awaiting plate clear')
                                  : hmsCount > 0
                                    ? `${hmsCount} HMS alert(s)`
                                    : st?.state === 'PAUSE'
                                      ? t('dashboard.printers.paused', 'Print paused')
                                      : ''}
                              </p>
                            </div>
                            {st?.awaiting_plate_clear && hasPermission('printers:clear_plate') ? (
                              <button
                                type="button"
                                onClick={() => clearPlateMutation.mutate(p.id)}
                                disabled={clearPlateMutation.isPending}
                                className="px-2.5 py-1 rounded-lg text-xs font-medium bg-bambu-green/15 text-bambu-green hover:bg-bambu-green hover:text-black transition-colors shrink-0"
                              >
                                {t('dashboard.printers.clearPlate', 'Clear Plate')}
                              </button>
                            ) : (
                              <Link
                                to="/printers"
                                className="text-xs text-bambu-green hover:underline shrink-0"
                              >
                                {t('common.view', 'View')}
                              </Link>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Low-Stock Spools */}
              <Card className="border-bambu-dark-tertiary">
                <CardContent className="p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                      <Disc3 className="w-4 h-4 text-purple-400" />
                      {t('dashboard.admin.lowStockSpools', 'Low-Stock Spools')}
                    </h3>
                    <span className="text-xs px-2 py-0.5 rounded-full bg-purple-500/15 text-purple-400 font-medium">
                      {lowStockSpools.length}
                    </span>
                  </div>
                  {lowStockSpools.length === 0 ? (
                    <p className="text-xs text-bambu-gray py-3">
                      {t('dashboard.admin.noLowStock', 'All spools are above low-stock thresholds.')}
                    </p>
                  ) : (
                    <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                      {lowStockSpools.slice(0, 6).map((s) => {
                        const labelW = s.label_weight || 1000;
                        const rem = Math.max(0, Math.round(labelW - (s.weight_used || 0)));
                        return (
                          <div
                            key={s.id}
                            className="p-2.5 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary flex items-center justify-between gap-2"
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <span
                                className="w-3.5 h-3.5 rounded-full border border-white/20 shrink-0"
                                style={{
                                  backgroundColor: s.rgba ? `#${s.rgba.slice(0, 6)}` : '#888888',
                                }}
                              />
                              <div className="min-w-0">
                                <p className="text-xs font-medium text-white truncate">
                                  {[s.brand, s.material, s.subtype, s.color_name]
                                    .filter(Boolean)
                                    .join(' ')}
                                </p>
                                <p className="text-[11px] text-red-400">
                                  {rem}g / {labelW}g remaining
                                </p>
                              </div>
                            </div>
                            <button
                              type="button"
                              onClick={() =>
                                setSpoolModalState({ open: true, mode: 'edit', spool: s })
                              }
                              className="text-xs text-bambu-green hover:underline shrink-0"
                            >
                              {t('common.edit', 'Edit')}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      )}

      {/* ── Accessible Printers Section (Point #3) ─────────────────────────── */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <PrinterIcon className="w-5 h-5 text-bambu-green" />
              {t('dashboard.printers.title', 'Accessible Printers')}
              <span className="text-xs font-normal text-bambu-gray">({printers.length})</span>
            </h2>
            <p className="text-xs text-bambu-gray">
              {t(
                'dashboard.printers.subtitle',
                'Live telemetry and availability for printers you are authorized to use.',
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {hasPermission('printers:create') && (
              <Button size="sm" variant="secondary" onClick={() => setShowAddPrinterModal(true)}>
                <Plus className="w-3.5 h-3.5" />
                {t('printers.addPrinter', 'Add Printer')}
              </Button>
            )}
            <Link
              to="/printers"
              className="text-xs text-bambu-green hover:underline flex items-center gap-1"
            >
              {t('dashboard.printers.openFullView', 'Full Printers View')}
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </div>

        {printersLoading ? (
          <div className="flex items-center justify-center py-8 text-bambu-gray gap-2">
            <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
            <span>{t('common.loading', 'Loading...')}</span>
          </div>
        ) : printers.length === 0 ? (
          <Card className="border-bambu-dark-tertiary">
            <CardContent className="p-8 text-center space-y-2">
              <PrinterIcon className="w-8 h-8 text-bambu-gray mx-auto" />
              <p className="text-sm text-white font-medium">
                {t('dashboard.printers.noAccessiblePrinters', 'No accessible printers found')}
              </p>
              <p className="text-xs text-bambu-gray max-w-md mx-auto">
                {t(
                  'dashboard.printers.noAccessiblePrintersHint',
                  'Ask an administrator to grant your account or group access to a printer, or add a new printer if you have permission.',
                )}
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {printers.map((printer) => {
              const status = statusByPrinterId[printer.id];
              const isConnected = !!status?.connected;
              const state = status?.state || 'UNKNOWN';
              const isRestricted =
                (printer.allowed_group_ids?.length ?? 0) > 0 ||
                (printer.allowed_user_ids?.length ?? 0) > 0;

              const stateBadge = {
                label: !printer.is_active
                  ? t('printers.status.maintenance', 'Maintenance')
                  : !isConnected
                    ? t('printers.status.offline', 'Offline')
                    : state === 'RUNNING'
                      ? t('printers.status.printing', 'Printing')
                      : state === 'PAUSE'
                        ? t('printers.status.paused', 'Paused')
                        : status?.awaiting_plate_clear
                          ? t('dashboard.printers.awaitingClear', 'Clear Plate')
                          : t('printers.status.idle', 'Ready'),
                className: !printer.is_active
                  ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                  : !isConnected
                    ? 'bg-zinc-500/15 text-bambu-gray border-zinc-500/30'
                    : state === 'RUNNING'
                      ? 'bg-bambu-green/15 text-bambu-green border-bambu-green/30'
                      : state === 'PAUSE' || status?.awaiting_plate_clear
                        ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                        : 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
              };

              // Collect loaded trays (AMS + External)
              const loadedTrays: Array<{ key: string; type: string; color: string }> = [];
              for (const ams of status?.ams ?? []) {
                for (const tray of ams.tray ?? []) {
                  if (tray.tray_type) {
                    loadedTrays.push({
                      key: `ams-${ams.id}-${tray.id}`,
                      type: tray.tray_type,
                      color: tray.tray_color ? `#${tray.tray_color.slice(0, 6)}` : '#888888',
                    });
                  }
                }
              }
              for (const vt of status?.vt_tray ?? []) {
                if (vt.tray_type) {
                  loadedTrays.push({
                    key: `vt-${vt.id}`,
                    type: vt.tray_type,
                    color: vt.tray_color ? `#${vt.tray_color.slice(0, 6)}` : '#888888',
                  });
                }
              }

              return (
                <Card key={printer.id} className="border-bambu-dark-tertiary flex flex-col">
                  <CardContent className="p-4 flex-1 flex flex-col justify-between gap-3">
                    <div className="space-y-2.5">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <Link
                              to="/printers"
                              className="text-base font-semibold text-white hover:text-bambu-green truncate transition-colors"
                            >
                              {printer.name}
                            </Link>
                            <span
                              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border ${stateBadge.className}`}
                            >
                              {stateBadge.label}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 text-xs text-bambu-gray mt-0.5 flex-wrap">
                            {printer.model && <span>{printer.model}</span>}
                            {printer.location && <span>· {printer.location}</span>}
                            <span
                              className={`inline-flex items-center gap-1 text-[11px] ${
                                isRestricted ? 'text-amber-400' : 'text-bambu-gray'
                              }`}
                              title={
                                isRestricted
                                  ? t(
                                      'printers.accessControl.restrictedTooltip',
                                      'Restricted to selected groups/users',
                                    )
                                  : t(
                                      'printers.accessControl.openTooltip',
                                      'Open access to all makers',
                                    )
                              }
                            >
                              ·{' '}
                              {isRestricted ? (
                                <>
                                  <Lock className="w-3 h-3" />
                                  {t('printers.accessControl.restricted', 'Restricted')}
                                </>
                              ) : (
                                <>
                                  <Globe className="w-3 h-3" />
                                  {t('printers.accessControl.openAccess', 'Open Access')}
                                </>
                              )}
                            </span>
                          </div>
                        </div>

                        {hasPermission('printers:update') && (
                          <button
                            type="button"
                            onClick={() => setEditingPrinter(printer)}
                            className="p-1.5 rounded-lg text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary transition-colors"
                            title={t('printers.editPrinter', 'Edit Printer & Access')}
                          >
                            <Edit2 className="w-4 h-4" />
                          </button>
                        )}
                      </div>

                      {/* Active Print Progress or Idle Summary */}
                      {isConnected && (state === 'RUNNING' || state === 'PAUSE') ? (
                        <div className="p-2.5 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary space-y-1.5">
                          <div className="flex items-center justify-between text-xs">
                            <span className="text-white font-medium truncate">
                              {status?.subtask_name || status?.gcode_file || 'Printing...'}
                            </span>
                            <span className="text-bambu-green font-semibold shrink-0 ml-2">
                              {Math.round(status?.progress ?? 0)}%
                            </span>
                          </div>
                          <div className="w-full h-1.5 bg-bambu-dark-tertiary rounded-full overflow-hidden">
                            <div
                              className="h-full bg-bambu-green rounded-full transition-all"
                              style={{ width: `${Math.min(100, Math.max(0, status?.progress ?? 0))}%` }}
                            />
                          </div>
                          <div className="flex items-center justify-between text-[11px] text-bambu-gray">
                            <span>
                              {status?.layer_num != null && status?.total_layers
                                ? `Layer ${status.layer_num}/${status.total_layers}`
                                : ''}
                            </span>
                            {status?.remaining_time != null && status.remaining_time > 0 && (
                              <span>{formatDurationMinutes(status.remaining_time)} left</span>
                            )}
                          </div>
                        </div>
                      ) : status?.awaiting_plate_clear ? (
                        <div className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-center justify-between gap-2">
                          <span className="text-xs text-amber-300">
                            {t(
                              'dashboard.printers.plateNeedsClearing',
                              'Finished print — confirm plate is cleared for next job.',
                            )}
                          </span>
                          {hasPermission('printers:clear_plate') && (
                            <Button
                              size="sm"
                              onClick={() => clearPlateMutation.mutate(printer.id)}
                              disabled={clearPlateMutation.isPending}
                            >
                              {t('dashboard.printers.clearPlate', 'Clear Plate')}
                            </Button>
                          )}
                        </div>
                      ) : null}

                      {/* Loaded Filaments Strip */}
                      {loadedTrays.length > 0 && (
                        <div className="flex items-center gap-1.5 flex-wrap pt-0.5">
                          <span className="text-[11px] text-bambu-gray mr-1">
                            {t('dashboard.printers.loadedFilaments', 'Loaded:')}
                          </span>
                          {loadedTrays.slice(0, 6).map((tray) => (
                            <span
                              key={tray.key}
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-bambu-dark border border-bambu-dark-tertiary text-[11px] text-white"
                            >
                              <span
                                className="w-2.5 h-2.5 rounded-full border border-white/20"
                                style={{ backgroundColor: tray.color }}
                              />
                              {tray.type}
                            </span>
                          ))}
                          {loadedTrays.length > 6 && (
                            <span className="text-[11px] text-bambu-gray">
                              +{loadedTrays.length - 6}
                            </span>
                          )}
                        </div>
                      )}
                    </div>

                    {/* Card Footer */}
                    <div className="pt-2 border-t border-bambu-dark-tertiary flex items-center justify-between text-xs text-bambu-gray">
                      <span>
                        {isConnected && status?.temperatures?.nozzle != null
                          ? `Nozzle ${Math.round(status.temperatures.nozzle)}°C · Bed ${Math.round(
                              status.temperatures.bed ?? 0,
                            )}°C`
                          : printer.ip_address}
                      </span>
                      <Link
                        to="/printers"
                        className="text-bambu-green hover:underline font-medium"
                      >
                        {t('dashboard.printers.manage', 'Open Controls →')}
                      </Link>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      {/* ── Main Two-Column Workspace: Projects & Files (Point #1) + Allowed Spools (Point #2) ── */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        {/* Left Column (7 cols): Files & Projects Control Center */}
        <div className="xl:col-span-7 space-y-6">
          {/* Accessible Projects */}
          <Card className="border-bambu-dark-tertiary">
            <CardContent className="p-5 space-y-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h2 className="text-base font-semibold text-white flex items-center gap-2">
                    <FolderKanban className="w-5 h-5 text-blue-400" />
                    {t('dashboard.projects.title', 'Accessible Projects')}
                    <span className="text-xs font-normal text-bambu-gray">({projects.length})</span>
                  </h2>
                  <p className="text-xs text-bambu-gray">
                    {t(
                      'dashboard.projects.subtitle',
                      'Projects you own or have been granted access to collaborate on.',
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Link
                    to="/projects"
                    className="text-xs text-bambu-green hover:underline flex items-center gap-1"
                  >
                    {t('dashboard.projects.viewAll', 'All Projects')}
                    <ArrowRight className="w-3.5 h-3.5" />
                  </Link>
                </div>
              </div>

              {projectsLoading ? (
                <div className="py-6 flex justify-center">
                  <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
                </div>
              ) : projects.length === 0 ? (
                <div className="p-6 rounded-xl bg-bambu-dark border border-bambu-dark-tertiary text-center space-y-2">
                  <p className="text-sm text-white font-medium">
                    {t('dashboard.projects.empty', 'No active projects yet')}
                  </p>
                  <p className="text-xs text-bambu-gray">
                    {t(
                      'dashboard.projects.emptyHint',
                      'Create a project to organize parts, track progress, and share access with your team.',
                    )}
                  </p>
                  {hasPermission('projects:create') && (
                    <div className="pt-1">
                      <Button size="sm" onClick={() => navigate('/projects')}>
                        <Plus className="w-3.5 h-3.5" />
                        {t('dashboard.projects.createFirst', 'Go to Projects')}
                      </Button>
                    </div>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {projects.slice(0, 6).map((proj) => {
                    const completed = proj.completed_count ?? 0;
                    const target = proj.target_count ?? proj.archive_count ?? 0;
                    const pct =
                      target > 0 ? Math.min(100, Math.round((completed / target) * 100)) : 0;
                    return (
                      <Link
                        key={proj.id}
                        to={`/projects/${proj.id}`}
                        className="p-3.5 rounded-xl bg-bambu-dark border border-bambu-dark-tertiary hover:border-bambu-green/50 transition-all flex flex-col justify-between gap-2.5 group"
                      >
                        <div>
                          <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-2 min-w-0">
                              <span
                                className="w-2.5 h-2.5 rounded-full shrink-0"
                                style={{ backgroundColor: proj.color || '#00ae42' }}
                              />
                              <h3 className="text-sm font-semibold text-white group-hover:text-bambu-green truncate transition-colors">
                                {proj.name}
                              </h3>
                            </div>
                            {proj.priority && proj.priority !== 'normal' && (
                              <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 font-medium shrink-0">
                                {proj.priority}
                              </span>
                            )}
                          </div>
                          {proj.description && (
                            <p className="text-xs text-bambu-gray line-clamp-1 mt-1">
                              {proj.description}
                            </p>
                          )}
                        </div>

                        <div className="space-y-1.5">
                          {target > 0 && (
                            <div className="w-full h-1.5 bg-bambu-dark-tertiary rounded-full overflow-hidden">
                              <div
                                className="h-full bg-bambu-green rounded-full"
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                          )}
                          <div className="flex items-center justify-between text-[11px] text-bambu-gray">
                            <span>
                              {completed} {t('dashboard.projects.prints', 'prints')}
                              {proj.queue_count ? ` · ${proj.queue_count} queued` : ''}
                            </span>
                            {proj.due_date && (
                              <span>
                                Due {parseUTCDate(proj.due_date)?.toLocaleDateString() ?? ''}
                              </span>
                            )}
                          </div>
                        </div>
                      </Link>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Accessible Files Control Center */}
          <Card className="border-bambu-dark-tertiary">
            <CardContent className="p-5 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-white flex items-center gap-2">
                    <FolderOpen className="w-5 h-5 text-bambu-green" />
                    {t('dashboard.files.title', 'Accessible Files & Print Library')}
                    <span className="text-xs font-normal text-bambu-gray">
                      ({libraryFiles.length})
                    </span>
                  </h2>
                  <p className="text-xs text-bambu-gray">
                    {t(
                      'dashboard.files.subtitle',
                      'Quickly queue or manage 3MF, G-code, and 3D model files you have access to.',
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="secondary" onClick={() => navigate('/files')}>
                    <FolderOpen className="w-3.5 h-3.5" />
                    {t('dashboard.files.openFileManager', 'Open File Manager')}
                  </Button>
                </div>
              </div>

              {libraryFiles.length > 0 && (
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50" />
                  <input
                    type="search"
                    value={fileSearch}
                    onChange={(e) => setFileSearch(e.target.value)}
                    placeholder={t('dashboard.files.searchPlaceholder', 'Search your accessible files...')}
                    className="w-full pl-9 pr-4 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                  />
                </div>
              )}

              {filesLoading ? (
                <div className="py-6 flex justify-center">
                  <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
                </div>
              ) : filteredFiles.length === 0 ? (
                <div className="p-6 rounded-xl bg-bambu-dark border border-bambu-dark-tertiary text-center space-y-2">
                  <FileText className="w-7 h-7 text-bambu-gray mx-auto" />
                  <p className="text-sm text-white font-medium">
                    {fileSearch
                      ? t('dashboard.files.noMatchingFiles', 'No matching files found')
                      : t('dashboard.files.noFiles', 'No files in your library yet')}
                  </p>
                  <Button size="sm" variant="secondary" onClick={() => navigate('/files')}>
                    {t('dashboard.files.uploadInFileManager', 'Upload Files in File Manager')}
                  </Button>
                </div>
              ) : (
                <div className="divide-y divide-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-xl overflow-hidden bg-bambu-dark">
                  {filteredFiles.map((file: LibraryFileListItem) => {
                    const isPrintable =
                      file.file_type === '3mf' ||
                      file.file_type === 'gcode' ||
                      file.filename.toLowerCase().endsWith('.3mf') ||
                      file.filename.toLowerCase().endsWith('.gcode');
                    return (
                      <div
                        key={file.id}
                        className="p-3 flex items-center justify-between gap-3 hover:bg-bambu-dark-secondary/60 transition-colors"
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          {file.thumbnail_path ? (
                            <img
                              src={withMediaToken(api.getLibraryFileThumbnailUrl(file.id))}
                              alt=""
                              className="w-10 h-10 rounded-lg object-cover bg-bambu-dark-secondary border border-bambu-dark-tertiary shrink-0"
                            />
                          ) : (
                            <div className="w-10 h-10 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary flex items-center justify-center shrink-0">
                              <FileText className="w-5 h-5 text-bambu-gray" />
                            </div>
                          )}
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-white truncate">
                              {file.print_name || file.filename}
                            </p>
                            <div className="flex items-center gap-2 text-xs text-bambu-gray flex-wrap">
                              <span className="uppercase text-[10px] px-1.5 py-0.2 rounded bg-bambu-dark-tertiary text-bambu-gray">
                                {file.file_type}
                              </span>
                              {file. sliced_for_model && <span>· {file.sliced_for_model}</span>}
                              {file.print_time_seconds != null && file.print_time_seconds > 0 && (
                                <span>
                                  · {formatDurationMinutes(Math.round(file.print_time_seconds / 60))}
                                </span>
                              )}
                              {file.filament_used_grams != null &&
                                file.filament_used_grams > 0 && (
                                  <span>· {Math.round(file.filament_used_grams)}g</span>
                                )}
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center gap-2 shrink-0">
                          {isPrintable && hasPermission('queue:create') && (
                            <Button
                              size="sm"
                              onClick={() =>
                                setPrintTarget({
                                  libraryFileId: file.id,
                                  name: file.print_name || file.filename,
                                })
                              }
                            >
                              <Play className="w-3.5 h-3.5" />
                              {t('dashboard.files.print', 'Print')}
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() =>
                              navigate(
                                file.folder_id ? `/files?folder=${file.folder_id}` : '/files',
                              )
                            }
                          >
                            {t('common.open', 'Open')}
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right Column (5 cols): Allowed Spools Management (Point #2) + My Queue & Recent Prints */}
        <div className="xl:col-span-5 space-y-6">
          {/* Manage Allowed Spools */}
          <Card className="border-bambu-dark-tertiary">
            <CardContent className="p-5 space-y-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h2 className="text-base font-semibold text-white flex items-center gap-2">
                    <Disc3 className="w-5 h-5 text-purple-400" />
                    {t('dashboard.spools.title', 'Allowed Spools')}
                    <span className="text-xs font-normal text-bambu-gray">({spools.length})</span>
                  </h2>
                  <p className="text-xs text-bambu-gray">
                    {t(
                      'dashboard.spools.subtitle',
                      'Manage and load filament spools you are authorized to use.',
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {hasPermission('inventory:create') && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() =>
                        setSpoolModalState({ open: true, mode: 'create', spool: null })
                      }
                    >
                      <Plus className="w-3.5 h-3.5" />
                      {t('dashboard.spools.add', 'Add')}
                    </Button>
                  )}
                  <Link
                    to="/inventory"
                    className="text-xs text-bambu-green hover:underline flex items-center gap-1"
                  >
                    {t('nav.inventory', 'Inventory')}
                    <ArrowRight className="w-3.5 h-3.5" />
                  </Link>
                </div>
              </div>

              {/* Search + Material Filter */}
              {spools.length > 0 && (
                <div className="flex items-center gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50" />
                    <input
                      type="search"
                      value={spoolSearch}
                      onChange={(e) => setSpoolSearch(e.target.value)}
                      placeholder={t('dashboard.spools.searchPlaceholder', 'Search spools...')}
                      className="w-full pl-9 pr-3 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-xs text-white placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                    />
                  </div>
                  <select
                    value={spoolMaterialFilter}
                    onChange={(e) => setSpoolMaterialFilter(e.target.value)}
                    className="px-2.5 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-xs text-white focus:outline-none focus:border-bambu-green"
                  >
                    <option value="all">{t('dashboard.spools.allMaterials', 'All Materials')}</option>
                    {spoolMaterials.map((mat) => (
                      <option key={mat} value={mat}>
                        {mat}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {spoolsLoading ? (
                <div className="py-6 flex justify-center">
                  <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
                </div>
              ) : filteredSpools.length === 0 ? (
                <div className="p-6 rounded-xl bg-bambu-dark border border-bambu-dark-tertiary text-center space-y-2">
                  <Disc3 className="w-7 h-7 text-bambu-gray mx-auto" />
                  <p className="text-sm text-white font-medium">
                    {t('dashboard.spools.noSpools', 'No matching allowed spools')}
                  </p>
                </div>
              ) : (
                <div className="space-y-2.5 max-h-[420px] overflow-y-auto pr-1">
                  {filteredSpools.slice(0, 12).map((spool) => {
                    const labelWeight = spool.label_weight || 1000;
                    const remaining = Math.max(
                      0,
                      Math.round(labelWeight - (spool.weight_used || 0)),
                    );
                    const pct = Math.min(100, Math.round((remaining / labelWeight) * 100));
                    const assignments = (assignmentsBySpoolId.get(spool.id) ?? []).filter(
                      (a) => accessiblePrinterIds.has(a.printer_id),
                    );
                    const isRestricted =
                      (spool.allowed_group_ids?.length ?? 0) > 0 ||
                      (spool.allowed_user_ids?.length ?? 0) > 0;

                    return (
                      <div
                        key={spool.id}
                        className="p-3 rounded-xl bg-bambu-dark border border-bambu-dark-tertiary space-y-2"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-center gap-2.5 min-w-0">
                            <span
                              className="w-5 h-5 rounded-full border border-white/20 shrink-0 shadow-inner"
                              style={{
                                backgroundColor: spool.rgba
                                  ? `#${spool.rgba.slice(0, 6)}`
                                  : '#888888',
                              }}
                            />
                            <div className="min-w-0">
                              <div className="flex items-center gap-1.5 flex-wrap">
                                <span className="text-xs font-semibold text-white truncate">
                                  {[spool.brand, spool.material, spool.subtype]
                                    .filter(Boolean)
                                    .join(' ')}
                                </span>
                                {spool.color_name && (
                                  <span className="text-xs text-bambu-gray truncate">
                                    · {spool.color_name}
                                  </span>
                                )}
                              </div>
                              <div className="flex items-center gap-2 text-[11px] text-bambu-gray mt-0.5 flex-wrap">
                                <span>
                                  {remaining}g / {labelWeight}g ({pct}%)
                                </span>
                                {spool.storage_location && <span>· {spool.storage_location}</span>}
                                {isRestricted && (
                                  <span className="inline-flex items-center gap-0.5 text-amber-400">
                                    · <Lock className="w-2.5 h-2.5" /> Restricted
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-1 shrink-0">
                            {hasPermission('inventory:update') && printers.length > 0 && (
                              <button
                                type="button"
                                onClick={() => setAssigningSpool(spool)}
                                className="px-2 py-1 rounded-lg text-[11px] font-medium bg-bambu-green/15 text-bambu-green hover:bg-bambu-green hover:text-black transition-colors flex items-center gap-1"
                                title={t('dashboard.spools.assignToAms', 'Load / Assign to AMS')}
                              >
                                <Layers className="w-3 h-3" />
                                {t('dashboard.spools.load', 'Load')}
                              </button>
                            )}
                            {hasPermission('inventory:update') && (
                              <button
                                type="button"
                                onClick={() =>
                                  setSpoolModalState({ open: true, mode: 'edit', spool })
                                }
                                className="p-1.5 rounded-lg text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary transition-colors"
                                title={t('dashboard.spools.editSpool', 'Edit Spool')}
                              >
                                <Edit2 className="w-3.5 h-3.5" />
                              </button>
                            )}
                          </div>
                        </div>

                        {/* Weight bar + loaded slot badges */}
                        <div className="space-y-1.5">
                          <div className="w-full h-1.5 bg-bambu-dark-tertiary rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${
                                pct <= 15
                                  ? 'bg-red-500'
                                  : pct <= 30
                                    ? 'bg-amber-500'
                                    : 'bg-bambu-green'
                              }`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          {assignments.length > 0 && (
                            <div className="flex items-center gap-1.5 flex-wrap">
                              {assignments.map((a) => (
                                <span
                                  key={a.id}
                                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-bambu-green/15 text-bambu-green border border-bambu-green/30"
                                >
                                  <PrinterIcon className="w-2.5 h-2.5" />
                                  {a.printer_name || `Printer #${a.printer_id}`} ·{' '}
                                  {getAmsSlotLabel(a.ams_id, a.tray_id)}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Active Queue & Recent Prints */}
          <Card className="border-bambu-dark-tertiary">
            <CardContent className="p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-white flex items-center gap-2">
                  <Activity className="w-5 h-5 text-amber-400" />
                  {t('dashboard.activity.title', 'Queue & Recent Prints')}
                </h2>
                <div className="flex items-center gap-3 text-xs">
                  <Link to="/queue" className="text-bambu-green hover:underline">
                    {t('nav.queue', 'Queue')} ({activeQueueItems.length})
                  </Link>
                  <Link to="/archives" className="text-bambu-gray hover:text-white">
                    {t('nav.archives', 'History')}
                  </Link>
                </div>
              </div>

              {/* Active Queue Items */}
              {activeQueueItems.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-bambu-gray uppercase tracking-wider">
                    {t('dashboard.activity.activeJobs', 'Active / Queued Jobs')}
                  </p>
                  {activeQueueItems.slice(0, 4).map((item) => (
                    <div
                      key={item.id}
                      className="p-2.5 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary flex items-center justify-between gap-2"
                    >
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-white truncate">
                          {item.archive_name || item.library_file_name || `Job #${item.id}`}
                        </p>
                        <p className="text-[11px] text-bambu-gray truncate">
                          {item.printer_name || item.target_model || 'Any matching printer'}
                        </p>
                      </div>
                      <span
                        className={`text-[10px] font-medium px-2 py-0.5 rounded-full border shrink-0 ${
                          item.status === 'printing'
                            ? 'bg-bambu-green/15 text-bambu-green border-bambu-green/30'
                            : item.status === 'pending_approval'
                              ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                              : 'bg-blue-500/15 text-blue-400 border-blue-500/30'
                        }`}
                      >
                        {item.status === 'pending_approval'
                          ? t('dashboard.activity.awaitingApproval', 'Awaiting Approval')
                          : item.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Recent Print History */}
              <div className="space-y-2">
                <p className="text-xs font-medium text-bambu-gray uppercase tracking-wider">
                  {t('dashboard.activity.recentPrints', 'Recent Prints')}
                </p>
                {recentArchives.length === 0 ? (
                  <p className="text-xs text-bambu-gray py-2">
                    {t('dashboard.activity.noRecentPrints', 'No recent print history yet.')}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {recentArchives.slice(0, 4).map((arch: Archive) => (
                      <div
                        key={arch.id}
                        className="p-2.5 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary flex items-center justify-between gap-2"
                      >
                        <div className="flex items-center gap-2.5 min-w-0">
                          {arch.status === 'completed' ? (
                            <CheckCircle2 className="w-4 h-4 text-bambu-green shrink-0" />
                          ) : (
                            <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                          )}
                          <div className="min-w-0">
                            <p className="text-xs font-medium text-white truncate">
                              {arch.print_name || arch.filename}
                            </p>
                            <p className="text-[11px] text-bambu-gray truncate">
                              {arch.filament_type || 'Print'}
                              {arch.filament_used_grams
                                ? ` · ${Math.round(arch.filament_used_grams)}g`
                                : ''}
                            </p>
                          </div>
                        </div>
                        {hasPermission('queue:create') && (
                          <button
                            type="button"
                            onClick={() =>
                              setPrintTarget({
                                archiveId: arch.id,
                                name: arch.print_name || arch.filename,
                              })
                            }
                            className="px-2 py-1 rounded-lg text-[11px] font-medium bg-bambu-dark-secondary hover:bg-bambu-dark-tertiary text-white border border-bambu-dark-tertiary flex items-center gap-1 shrink-0 transition-colors"
                            title={t('dashboard.activity.reprint', 'Reprint')}
                          >
                            <RotateCcw className="w-3 h-3 text-bambu-green" />
                            {t('dashboard.activity.reprint', 'Reprint')}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ── Modals ──────────────────────────────────────────────────────────── */}
      {showAddPrinterModal && (
        <AddPrinterModal
          onClose={() => setShowAddPrinterModal(false)}
          onAdd={(data) => addPrinterMutation.mutate(data)}
          existingSerials={printers.map((p) => p.serial_number)}
        />
      )}

      {editingPrinter && (
        <EditPrinterModal
          printer={editingPrinter}
          onClose={() => setEditingPrinter(null)}
        />
      )}

      {spoolModalState.open && (
        <SpoolFormModal
          isOpen={spoolModalState.open}
          onClose={() => setSpoolModalState({ open: false, mode: 'create', spool: null })}
          spool={spoolModalState.spool}
          mode={spoolModalState.mode}
          currencySymbol={currencySymbol}
          spoolsQueryKey={['spools', 'authorized']}
        />
      )}

      {assigningSpool && (
        <AssignToAmsModal
          isOpen={!!assigningSpool}
          onClose={() => setAssigningSpool(null)}
          spool={assigningSpool}
          printerId={printers[0]?.id ?? null}
          variant="dialog"
        />
      )}

      {printTarget && (
        <PrintModal
          mode="create"
          archiveId={printTarget.archiveId}
          libraryFileId={printTarget.libraryFileId}
          archiveName={printTarget.name}
          onClose={() => setPrintTarget(null)}
        />
      )}
    </div>
  );
}
