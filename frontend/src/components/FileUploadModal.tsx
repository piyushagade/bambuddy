import { useState, useRef, useEffect, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Upload,
  X,
  File,
  Loader2,
  CheckCircle,
  XCircle,
  Archive as ArchiveIcon,
  Printer,
  Image,
  Briefcase,
  AlertTriangle,
  Plus,
} from 'lucide-react';
import { api } from '../api/client';
import type { LibraryFileUploadResponse, ProjectCreate } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { getCurrencySymbol } from '../utils/currency';
import { ProjectModal } from '../pages/ProjectsPage';
import { Button } from './Button';

interface UploadFile {
  file: File;
  status: 'pending' | 'uploading' | 'success' | 'error';
  error?: string;
  isZip?: boolean;
  is3mf?: boolean;
  extractedCount?: number;
}

interface FileUploadModalProps {
  folderId: number | null;
  projectId?: number | null;
  onClose: () => void;
  onUploadComplete: () => void;
  /** Called after each file is successfully uploaded with its response data. Return a string to show an error and prevent modal from closing. */
  onFileUploaded?: (file: LibraryFileUploadResponse) => string | void;
  /** When true, automatically uploads the file as soon as it's added and closes the modal */
  autoUpload?: boolean;
  /** Validate files before adding. Return a string to reject with an error message. */
  validateFile?: (file: File) => string | undefined;
  /** Restrict file picker to specific file types (e.g. ".gcode,.gcode.3mf") */
  accept?: string;
  /** Pre-seed the modal with files (e.g. from a page-wide drop) on first mount. */
  initialFiles?: File[];
}

export function FileUploadModal({
  folderId,
  projectId,
  onClose,
  onUploadComplete,
  onFileUploaded,
  autoUpload,
  validateFile,
  accept,
  initialFiles,
}: FileUploadModalProps) {
  const { t } = useTranslation();
  const { user, hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const canCreateProject = hasPermission('projects:create');
  const requiresProject = Boolean(
    user &&
      !user.is_admin &&
      !user.project_waiver &&
      (user.requires_project || (user.groups ?? []).some((g) => !g.is_expired && g.require_project)),
  );
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(projectId ?? null);
  const [projectConfirmed, setProjectConfirmed] = useState<boolean>(() => projectId != null || !requiresProject);
  const [showCreateProjectModal, setShowCreateProjectModal] = useState(false);
  const { data: activeProjects = [], isLoading: loadingProjects } = useQuery({
    queryKey: ['projects', 'active'],
    queryFn: () => api.getProjects('active'),
    enabled: requiresProject && !projectConfirmed,
  });
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    enabled: showCreateProjectModal,
  });
  const currencySymbol = getCurrencySymbol(settings?.currency || 'USD');

  const createProjectMutation = useMutation({
    mutationFn: (data: ProjectCreate) => api.createProject(data),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      setSelectedProjectId(created.id);
      setShowCreateProjectModal(false);
      showToast(t('projects.toast.created', 'Project created'), 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const [files, setFiles] = useState<UploadFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [preserveZipStructure, setPreserveZipStructure] = useState(true);
  const [createFolderFromZip, setCreateFolderFromZip] = useState(false);
  const [generateStlThumbnails, setGenerateStlThumbnails] = useState(true);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    addFiles(Array.from(e.dataTransfer.files));
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      addFiles(Array.from(e.target.files));
    }
  };

  const updateFileStatus = (file: File, update: Partial<UploadFile>) => {
    setFiles((prev) => prev.map((f) => (f.file === file ? { ...f, ...update } : f)));
  };

  const uploadFiles = async (filesToUpload: UploadFile[], overrideProjectId?: number | null) => {
    setIsUploading(true);
    const effectiveProjectId = overrideProjectId !== undefined ? overrideProjectId : selectedProjectId;

    for (const uf of filesToUpload) {
      if (uf.status !== 'pending') continue;

      updateFileStatus(uf.file, { status: 'uploading' });

      try {
        if (uf.isZip) {
          const result = await api.extractZipFile(
            uf.file,
            folderId,
            preserveZipStructure,
            createFolderFromZip,
            generateStlThumbnails,
            effectiveProjectId,
          );
          updateFileStatus(uf.file, {
            status: result.errors.length > 0 && result.extracted === 0 ? 'error' : 'success',
            extractedCount: result.extracted,
            error: result.errors.length > 0 ? t('fileManager.zipFilesFailed', '{{count}} files failed', { count: result.errors.length }) : undefined,
          });
        } else {
          const result = await api.uploadLibraryFile(
            uf.file,
            folderId,
            generateStlThumbnails,
            effectiveProjectId,
          );
          updateFileStatus(uf.file, { status: 'success' });
          const error = onFileUploaded?.(
            effectiveProjectId != null && result.project_id === undefined
              ? { ...result, project_id: effectiveProjectId }
              : result,
          );
          if (error) {
            setUploadError(error);
            setFiles([]);
            setIsUploading(false);
            return;
          }
        }
      } catch (err) {
        updateFileStatus(uf.file, {
          status: 'error',
          error: err instanceof Error ? err.message : t('fileManager.uploadFailed', 'Upload failed'),
        });
      }
    }

    setIsUploading(false);
    onUploadComplete();
    // #1401: don't auto-close if any file ended with an error — the user
    // needs to see the rejection message (e.g. "raw .gcode upload"), not
    // have the modal vanish before they can read it. Closing happens via
    // the X / Close button instead, after the user has seen what failed.
    setFiles((prev) => {
      const anyFailed = prev.some((f) => f.status === 'error');
      if (!anyFailed) {
        onClose();
      }
      return prev;
    });
  };

  const addFiles = (newFiles: File[], overrideProjectId?: number | null) => {
    setUploadError(null);
    if (validateFile) {
      for (const file of newFiles) {
        const error = validateFile(file);
        if (error) {
          setUploadError(error);
          return;
        }
      }
    }
    const toUpload: UploadFile[] = newFiles.map((file) => ({
      file,
      status: 'pending' as const,
      isZip: file.name.toLowerCase().endsWith('.zip'),
      is3mf: file.name.toLowerCase().endsWith('.3mf'),
    }));
    setFiles((prev) => [...prev, ...toUpload]);

    if (autoUpload && newFiles.length > 0) {
      uploadFiles(toUpload, overrideProjectId);
    }
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  // Seed once when project selection is confirmed (or immediately when no project
  // selection popup is needed) and the parent passed initialFiles (page-wide drop).
  // The ref/list shape means a subsequent re-render with the same files won't
  // double-add — only the first non-empty initialFiles arg ever flows through.
  const seededInitialRef = useRef(false);
  useEffect(() => {
    if (!projectConfirmed) return;
    if (seededInitialRef.current) return;
    if (!initialFiles || initialFiles.length === 0) return;
    seededInitialRef.current = true;
    addFiles(initialFiles, selectedProjectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectConfirmed]);

  if (requiresProject && !projectConfirmed) {
    return (
      <>
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-md border border-bambu-dark-tertiary">
            <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Briefcase className="w-5 h-5 text-bambu-green" />
                Select Project
              </h2>
              <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
                <X className="w-5 h-5 text-bambu-gray" />
              </button>
            </div>

            <div className="p-4 space-y-4">
              <div className="p-3 bg-amber-500/10 border border-amber-500/40 rounded-lg text-sm text-amber-200 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-amber-400" />
                <span>
                  Your group requires all uploaded models to belong to a project. Select a project below to continue.
                </span>
              </div>

              <div className="max-h-64 overflow-y-auto space-y-1 bg-bambu-dark rounded-lg p-2">
                {loadingProjects ? (
                  <div className="flex items-center justify-center py-6">
                    <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
                  </div>
                ) : activeProjects.length > 0 ? (
                  activeProjects.map((project) => (
                    <button
                      key={project.id}
                      type="button"
                      onClick={() => setSelectedProjectId(project.id)}
                      className={`w-full text-left px-3 py-2 rounded transition-colors flex items-center gap-2 ${
                        selectedProjectId === project.id
                          ? 'bg-bambu-green/20 text-bambu-green'
                          : 'hover:bg-bambu-dark-tertiary text-white'
                      }`}
                    >
                      <div
                        className="w-3 h-3 rounded-full flex-shrink-0"
                        style={{ backgroundColor: project.color || '#00ae42' }}
                      />
                      <span className="truncate flex-1">{project.name}</span>
                      {project.created_by_username && project.created_by_id !== user?.id && (
                        <span className="text-xs text-bambu-gray flex-shrink-0">
                          {project.created_by_username}
                        </span>
                      )}
                    </button>
                  ))
                ) : (
                  <p className="text-sm text-bambu-gray text-center py-4">
                    No projects added
                  </p>
                )}
              </div>
            </div>

            <div className="p-4 border-t border-bambu-dark-tertiary flex items-center justify-between gap-2">
              {canCreateProject ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setShowCreateProjectModal(true)}
                >
                  <Plus className="w-4 h-4 mr-1.5" />
                  {t('projects.newProject', 'New Project')}
                </Button>
              ) : (
                <div />
              )}
              <div className="flex gap-2">
                <Button variant="secondary" onClick={onClose}>
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={() => {
                    if (selectedProjectId != null) {
                      setProjectConfirmed(true);
                    }
                  }}
                  disabled={selectedProjectId == null || loadingProjects}
                >
                  Continue
                </Button>
              </div>
            </div>
          </div>
        </div>
        {showCreateProjectModal && (
          <ProjectModal
            onClose={() => setShowCreateProjectModal(false)}
            onSave={(data) => createProjectMutation.mutate(data as ProjectCreate)}
            isLoading={createProjectMutation.isPending}
            currencySymbol={currencySymbol}
            t={t as unknown as (key: string, options?: Record<string, unknown>) => string}
          />
        )}
      </>
    );
  }

  const hasZipFiles = files.some((f) => f.isZip && f.status === 'pending');
  const hasStlFiles = files.some((f) => f.file.name.toLowerCase().endsWith('.stl') && f.status === 'pending');
  const has3mfFiles = files.some((f) => f.is3mf && f.status === 'pending');
  const pendingCount = files.filter((f) => f.status === 'pending').length;
  const allDone = files.length > 0 && pendingCount === 0 && !isUploading;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-lg border border-bambu-dark-tertiary max-h-[90vh] flex flex-col">
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between flex-shrink-0">
          <h2 className="text-lg font-semibold text-white">{t('fileManager.uploadFiles')}</h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        <div className="p-4 space-y-4 overflow-y-auto flex-1">
          {/* Drop Zone */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
              isDragging
                ? 'border-bambu-green bg-bambu-green/10'
                : 'border-bambu-dark-tertiary hover:border-bambu-green/50'
            }`}
          >
            <Upload className={`w-10 h-10 mx-auto mb-3 ${isDragging ? 'text-bambu-green' : 'text-bambu-gray'}`} />
            <p className="text-white font-medium">
              {isDragging ? t('fileManager.dropFilesHere') : t('fileManager.dragDropFiles')}
            </p>
            <p className="text-sm text-bambu-gray mt-1">{t('fileManager.orClickToBrowse')}</p>
            <p className="text-xs text-bambu-gray/70 mt-2">{t('fileManager.allFileTypesSupported')}</p>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={accept}
            className="hidden"
            onChange={handleFileSelect}
          />

          {/* ZIP Options */}
          {hasZipFiles && (
            <div className="p-3 bg-blue-50 dark:bg-blue-500/10 border border-blue-300 dark:border-blue-500/30 rounded-lg">
              <div className="flex items-start gap-3">
                <ArchiveIcon className="w-5 h-5 text-blue-600 dark:text-blue-400 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  <p className="text-sm text-blue-700 dark:text-blue-300 font-medium">{t('fileManager.zipFilesDetected')}</p>
                  <p className="text-xs text-blue-700/80 dark:text-blue-300/70 mt-1">
                    {t('fileManager.zipExtractOptions')}
                  </p>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={preserveZipStructure}
                      onChange={(e) => setPreserveZipStructure(e.target.checked)}
                      className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    />
                    <span className="text-sm text-white">{t('fileManager.preserveZipStructure')}</span>
                  </label>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={createFolderFromZip}
                      onChange={(e) => setCreateFolderFromZip(e.target.checked)}
                      className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    />
                    <span className="text-sm text-white">{t('fileManager.createFolderFromZip')}</span>
                  </label>
                </div>
              </div>
            </div>
          )}

          {/* 3MF File Info */}
          {has3mfFiles && (
            <div className="p-3 bg-purple-50 dark:bg-purple-500/10 border border-purple-300 dark:border-purple-500/30 rounded-lg">
              <div className="flex items-start gap-3">
                <Printer className="w-5 h-5 text-purple-600 dark:text-purple-400 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  <p className="text-sm text-purple-700 dark:text-purple-300 font-medium">{t('fileManager.threemfDetected')}</p>
                  <p className="text-xs text-purple-700/80 dark:text-purple-300/70 mt-1">
                    {t('fileManager.threemfExtractionInfo')}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* STL Thumbnail Options */}
          {(hasStlFiles || hasZipFiles) && (
            <div className="p-3 bg-bambu-green/10 border border-bambu-green/30 rounded-lg">
              <div className="flex items-start gap-3">
                <Image className="w-5 h-5 text-bambu-green mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  <p className="text-sm text-bambu-green font-medium">{t('fileManager.stlThumbnailGeneration')}</p>
                  <p className="text-xs text-bambu-green/70 mt-1">
                    {hasZipFiles && !hasStlFiles
                      ? t('fileManager.zipMayContainStl')
                      : t('fileManager.thumbnailsCanBeGenerated')}
                  </p>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={generateStlThumbnails}
                      onChange={(e) => setGenerateStlThumbnails(e.target.checked)}
                      className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    />
                    <span className="text-sm text-white">{t('fileManager.generateThumbnailsForStl')}</span>
                  </label>
                </div>
              </div>
            </div>
          )}

          {/* File List */}
          {files.length > 0 && (
            <div className="max-h-48 overflow-y-auto space-y-2">
              {files.map((uploadFile, index) => (
                <div
                  key={index}
                  className="flex items-center gap-3 p-2 bg-bambu-dark rounded-lg"
                >
                  {uploadFile.isZip ? (
                    <ArchiveIcon className="w-4 h-4 text-blue-600 dark:text-blue-400 flex-shrink-0" />
                  ) : (
                    <File className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white truncate">{uploadFile.file.name}</p>
                    <p className="text-xs text-bambu-gray">
                      {(uploadFile.file.size / 1024 / 1024).toFixed(2)} MB
                      {uploadFile.isZip && uploadFile.status === 'pending' && (
                        <span className="text-blue-700 dark:text-blue-400 ml-2">• {t('fileManager.willBeExtracted')}</span>
                      )}
                      {uploadFile.extractedCount !== undefined && (
                        <span className="text-green-700 dark:text-green-400 ml-2">• {t('fileManager.filesExtracted', { count: uploadFile.extractedCount })}</span>
                      )}
                    </p>
                    {/* #1401: errors render inline rather than as a hover-only
                        title. The backend's rejection messages explain the
                        actual fix (re-export as .gcode.3mf) — useless if the
                        user can't read them. */}
                    {uploadFile.status === 'error' && uploadFile.error && (
                      <p className="text-xs text-red-700 dark:text-red-400 mt-1 break-words">{uploadFile.error}</p>
                    )}
                  </div>
                  {uploadFile.status === 'pending' && (
                    <button
                      onClick={() => removeFile(index)}
                      className="p-1 hover:bg-bambu-dark-tertiary rounded"
                    >
                      <X className="w-4 h-4 text-bambu-gray" />
                    </button>
                  )}
                  {uploadFile.status === 'uploading' && (
                    <Loader2 className="w-4 h-4 text-bambu-green animate-spin" />
                  )}
                  {uploadFile.status === 'success' && (
                    <CheckCircle className="w-4 h-4 text-green-500" />
                  )}
                  {uploadFile.status === 'error' && (
                    <span title={uploadFile.error}>
                      <XCircle className="w-4 h-4 text-red-500" />
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Compatibility Error */}
          {uploadError && (
            <div className="p-3 bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30 rounded-lg">
              <div className="flex items-start gap-3">
                <XCircle className="w-5 h-5 text-red-600 dark:text-red-400 mt-0.5 flex-shrink-0" />
                <p className="text-sm text-red-700 dark:text-red-300">{uploadError}</p>
              </div>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2 flex-shrink-0">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          {!allDone && (
            <Button
              onClick={() => uploadFiles(files)}
              disabled={pendingCount === 0 || isUploading}
            >
              {isUploading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  {t('fileManager.uploading')}
                </>
              ) : (
                <>
                  <Upload className="w-4 h-4 mr-2" />
                  {t('common.upload')} {pendingCount > 0 ? `(${pendingCount})` : ''}
                </>
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
