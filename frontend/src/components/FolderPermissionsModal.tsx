import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Shield, Users, User, Plus, Trash2, Loader2, Check, Search } from 'lucide-react';
import { api } from '../api/client';
import type {
  LibraryFolderTree,
  FolderPermissionGrant,
  FolderShareTargetUser,
  FolderShareTargetGroup,
} from '../api/client';
import { Button } from './Button';
import { PopupModal } from './PopupModal';
import { useToast } from '../contexts/ToastContext';

interface FolderPermissionsModalProps {
  folder: LibraryFolderTree;
  onClose: () => void;
}

interface EditableGrant {
  key: string;
  user_id?: number | null;
  username?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  group_id?: number | null;
  group_name?: string | null;
  can_read: boolean;
  can_write: boolean;
}

function formatUserDisplay(u: {
  username?: string | null;
  first_name?: string | null;
  last_name?: string | null;
}): string {
  const fullName = [u.first_name, u.last_name].filter(Boolean).join(' ').trim();
  if (fullName && u.username) {
    return `${fullName} (@${u.username})`;
  }
  return fullName || u.username || 'Unknown User';
}

export function FolderPermissionsModal({ folder, onClose }: FolderPermissionsModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { data, isLoading } = useQuery({
    queryKey: ['folder-permissions', folder.id],
    queryFn: () => api.getLibraryFolderPermissions(folder.id),
  });

  const [grants, setGrants] = useState<EditableGrant[]>([]);
  const [inheritToChildren, setInheritToChildren] = useState<boolean>(false);
  const [grantedFilterQuery, setGrantedFilterQuery] = useState('');

  // Popup selector modal state ('group' | 'user' | null)
  const [pickerMode, setPickerMode] = useState<'group' | 'user' | null>(null);
  const [pickerSearch, setPickerSearch] = useState('');
  const [pickerGroupFilter, setPickerGroupFilter] = useState<string>('');
  const [selectedPickerIds, setSelectedPickerIds] = useState<Set<number>>(new Set());
  const [pickerAccessLevel, setPickerAccessLevel] = useState<'read' | 'write'>('write');

  useEffect(() => {
    if (data) {
      setInheritToChildren(Boolean(data.inherit_to_children));
      setGrants(
        data.permissions.map((p) => ({
          key: p.group_id ? `group:${p.group_id}` : `user:${p.user_id}`,
          user_id: p.user_id,
          username: p.username,
          first_name: p.first_name,
          last_name: p.last_name,
          group_id: p.group_id,
          group_name: p.group_name,
          can_read: p.can_read,
          can_write: p.can_write,
        })),
      );
    }
  }, [data]);

  const openPicker = (mode: 'group' | 'user') => {
    setPickerMode(mode);
    setPickerSearch('');
    setPickerGroupFilter('');
    setSelectedPickerIds(new Set());
    setPickerAccessLevel('write');
  };

  const closePicker = () => {
    setPickerMode(null);
    setPickerSearch('');
    setPickerGroupFilter('');
    setSelectedPickerIds(new Set());
  };

  const saveMutation = useMutation({
    mutationFn: (payload: { permissions: FolderPermissionGrant[]; inheritToChildren: boolean }) =>
      api.updateLibraryFolderPermissions(folder.id, payload.permissions, payload.inheritToChildren),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['folder-permissions', folder.id] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      showToast('Folder permissions updated', 'success');
      onClose();
    },
    onError: (err: Error) => {
      showToast(err.message || 'Failed to update folder permissions', 'error');
    },
  });

  const grantedGroupIds = useMemo(
    () => new Set(grants.filter((g) => g.group_id).map((g) => g.group_id!)),
    [grants],
  );
  const grantedUserIds = useMemo(
    () => new Set(grants.filter((g) => g.user_id).map((g) => g.user_id!)),
    [grants],
  );

  const ungrantedGroups = useMemo(
    () => (data?.available_groups ?? []).filter((g) => !grantedGroupIds.has(g.id)),
    [data?.available_groups, grantedGroupIds],
  );

  const ungrantedUsers = useMemo(
    () =>
      (data?.available_users ?? []).filter(
        (u) => !grantedUserIds.has(u.id) && u.id !== data?.created_by_id,
      ),
    [data?.available_users, grantedUserIds, data?.created_by_id],
  );

  const filteredPickerGroups = useMemo(() => {
    const q = pickerSearch.trim().toLowerCase();
    if (!q) return ungrantedGroups;
    return ungrantedGroups.filter(
      (g) =>
        g.name.toLowerCase().includes(q) ||
        (g.description && g.description.toLowerCase().includes(q)),
    );
  }, [ungrantedGroups, pickerSearch]);

  const filteredPickerUsers = useMemo(() => {
    const q = pickerSearch.trim().toLowerCase();
    return ungrantedUsers.filter((u) => {
      if (pickerGroupFilter && !(u.group_names ?? []).includes(pickerGroupFilter)) {
        return false;
      }
      if (!q) return true;
      const fullName = [u.first_name, u.last_name].filter(Boolean).join(' ').toLowerCase();
      return (
        u.username.toLowerCase().includes(q) ||
        fullName.includes(q) ||
        (u.email && u.email.toLowerCase().includes(q)) ||
        (u.group_names ?? []).some((gn) => gn.toLowerCase().includes(q))
      );
    });
  }, [ungrantedUsers, pickerSearch, pickerGroupFilter]);

  const togglePickerItem = (id: number) => {
    setSelectedPickerIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const selectAllFiltered = () => {
    const ids =
      pickerMode === 'group'
        ? filteredPickerGroups.map((g) => g.id)
        : filteredPickerUsers.map((u) => u.id);
    setSelectedPickerIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.add(id);
      return next;
    });
  };

  const handleConfirmPicker = () => {
    if (selectedPickerIds.size === 0 || !pickerMode) return;
    if (pickerMode === 'group') {
      const byId = new Map<number, FolderShareTargetGroup>(
        (data?.available_groups ?? []).map((g) => [g.id, g]),
      );
      setGrants((prev) => {
        const next = [...prev];
        for (const gid of selectedPickerIds) {
          const groupObj = byId.get(gid);
          if (!groupObj) continue;
          const key = `group:${gid}`;
          const existingIdx = next.findIndex((item) => item.key === key);
          const entry: EditableGrant = {
            key,
            group_id: gid,
            group_name: groupObj.name,
            can_read: true,
            can_write: pickerAccessLevel === 'write',
          };
          if (existingIdx >= 0) {
            next[existingIdx] = entry;
          } else {
            next.push(entry);
          }
        }
        return next;
      });
    } else {
      const byId = new Map<number, FolderShareTargetUser>(
        (data?.available_users ?? []).map((u) => [u.id, u]),
      );
      setGrants((prev) => {
        const next = [...prev];
        for (const uid of selectedPickerIds) {
          const userObj = byId.get(uid);
          if (!userObj) continue;
          const key = `user:${uid}`;
          const existingIdx = next.findIndex((item) => item.key === key);
          const entry: EditableGrant = {
            key,
            user_id: uid,
            username: userObj.username,
            first_name: userObj.first_name,
            last_name: userObj.last_name,
            can_read: true,
            can_write: pickerAccessLevel === 'write',
          };
          if (existingIdx >= 0) {
            next[existingIdx] = entry;
          } else {
            next.push(entry);
          }
        }
        return next;
      });
    }
    closePicker();
  };

  const handleChangeLevel = (key: string, level: 'read' | 'write') => {
    setGrants((prev) =>
      prev.map((g) =>
        g.key === key
          ? { ...g, can_read: true, can_write: level === 'write' }
          : g,
      ),
    );
  };

  const handleRemoveGrant = (key: string) => {
    setGrants((prev) => prev.filter((g) => g.key !== key));
  };

  const handleSave = () => {
    const payload: FolderPermissionGrant[] = grants.map((g) => ({
      user_id: g.user_id ?? null,
      group_id: g.group_id ?? null,
      can_read: g.can_read,
      can_write: g.can_write,
    }));
    saveMutation.mutate({ permissions: payload, inheritToChildren });
  };

  const visibleGrants = useMemo(() => {
    const q = grantedFilterQuery.trim().toLowerCase();
    if (!q) return grants;
    return grants.filter((g) => {
      const label = g.group_id
        ? (g.group_name ?? '')
        : formatUserDisplay(g);
      return label.toLowerCase().includes(q);
    });
  }, [grants, grantedFilterQuery]);

  return (
    <>
      <PopupModal
        onClose={onClose}
        closeOnEscape={pickerMode === null}
        maxWidth="lg"
        bodyClassName="p-4 space-y-4"
        icon={<Shield className="w-5 h-5 text-bambu-green" />}
        title="Folder Access & Sharing"
        subtitle={
          <>
            Manage who can view or upload files in{' '}
            <span className="text-white font-medium">{folder.name}</span>
          </>
        }
        footer={
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleSave}
              disabled={isLoading || saveMutation.isPending}
            >
              {saveMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin mr-1.5" />
              ) : (
                <Check className="w-4 h-4 mr-1.5" />
              )}
              Save Permissions
            </Button>
          </div>
        }
      >
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
          </div>
        ) : (
          <>
            {/* Owner / Admin info */}
            <div className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary flex items-center justify-between text-xs">
              <div className="flex items-center gap-2 text-bambu-gray">
                <User className="w-4 h-4 text-bambu-green" />
                <span>Folder Owner:</span>
                <span className="text-white font-medium">
                  {data?.created_by_username || 'Administrators'}
                </span>
              </div>
              <span className="px-2 py-0.5 rounded bg-bambu-green/20 text-bambu-green font-medium">
                Full Access (Owner & Admins)
              </span>
            </div>

            {/* Subfolder inheritance checkbox (toggled off by default) */}
            <label className="flex items-start gap-2.5 p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary cursor-pointer hover:border-bambu-green/50 transition-colors">
              <input
                type="checkbox"
                checked={inheritToChildren}
                onChange={(e) => setInheritToChildren(e.target.checked)}
                className="mt-0.5 rounded border-bambu-dark-tertiary bg-bambu-dark-secondary text-bambu-green focus:ring-bambu-green"
              />
              <div className="text-xs">
                <div className="font-medium text-white">
                  Apply permissions to all subfolders (top-down inheritance)
                </div>
                <div className="text-bambu-gray mt-0.5">
                  When unchecked (default), granted users and groups only get access to{' '}
                  <span className="text-white">{folder.name}</span> itself and subfolders they create or are explicitly granted.
                </div>
              </div>
            </label>

            {/* Action buttons to open scalable Group / User selector popup modal */}
            <div className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary space-y-2">
              <div className="text-xs font-medium text-bambu-gray">
                Grant Access to Groups or Students
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => openPicker('group')}
                  className="flex items-center justify-center gap-2 py-2.5 px-3 rounded-lg text-xs font-medium border border-bambu-green/40 bg-bambu-green/10 text-bambu-green hover:bg-bambu-green/20 transition-colors"
                >
                  <Users className="w-4 h-4" />
                  <span>+ Share with Group / Cohort</span>
                </button>
                <button
                  type="button"
                  onClick={() => openPicker('user')}
                  className="flex items-center justify-center gap-2 py-2.5 px-3 rounded-lg text-xs font-medium border border-bambu-dark-tertiary bg-bambu-dark-secondary text-white hover:border-bambu-green/50 hover:text-bambu-green transition-colors"
                >
                  <User className="w-4 h-4" />
                  <span>+ Share with Student / User</span>
                </button>
              </div>
            </div>

            {/* Current permissions list */}
            <div>
              <div className="flex items-center justify-between gap-2 mb-2">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-bambu-gray">
                  Explicitly Granted Access ({grants.length})
                </h3>
                {grants.length > 5 && (
                  <div className="relative w-48">
                    <Search className="w-3.5 h-3.5 text-bambu-gray absolute left-2.5 top-1/2 -translate-y-1/2" />
                    <input
                      type="text"
                      value={grantedFilterQuery}
                      onChange={(e) => setGrantedFilterQuery(e.target.value)}
                      placeholder="Filter granted list..."
                      className="w-full pl-7 pr-2 py-1 text-xs bg-bambu-dark border border-bambu-dark-tertiary rounded text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                    />
                  </div>
                )}
              </div>
              {grants.length === 0 ? (
                <div className="text-center py-6 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary text-sm text-bambu-gray">
                  No groups or students have been granted explicit access to this folder yet.
                  <div className="text-xs mt-1 text-bambu-gray/70">
                    Only the folder owner, administrators, and anyone with access to a parent folder can access it.
                  </div>
                </div>
              ) : (
                <div className="space-y-1.5 max-h-60 overflow-y-auto">
                  {visibleGrants.map((g) => (
                    <div
                      key={g.key}
                      className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        {g.group_id ? (
                          <Users className="w-4 h-4 text-blue-400 flex-shrink-0" />
                        ) : (
                          <User className="w-4 h-4 text-bambu-green flex-shrink-0" />
                        )}
                        <span className="text-sm text-white truncate">
                          {g.group_id ? g.group_name : formatUserDisplay(g)}
                        </span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-bambu-dark-secondary text-bambu-gray flex-shrink-0">
                          {g.group_id ? 'Group' : 'User'}
                        </span>
                      </div>

                      <div className="flex items-center gap-2 flex-shrink-0">
                        <select
                          value={g.can_write ? 'write' : 'read'}
                          onChange={(e) =>
                            handleChangeLevel(g.key, e.target.value as 'read' | 'write')
                          }
                          aria-label="Permission level"
                          className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-bambu-green"
                        >
                          <option value="write">Read & Write</option>
                          <option value="read">Read Only</option>
                        </select>
                        <button
                          type="button"
                          onClick={() => handleRemoveGrant(g.key)}
                          className="p-1 text-bambu-gray hover:text-red-400 rounded hover:bg-bambu-dark-secondary transition-colors"
                          title="Remove access"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </PopupModal>

      {/* Scalable Search & Multi-Select Popup Modal for Groups or Students */}
      {pickerMode !== null && (
        <PopupModal
          onClose={closePicker}
          zIndex="z-[60]"
          maxWidth="xl"
          bodyClassName="p-3 space-y-1.5"
          icon={
            pickerMode === 'group' ? (
              <Users className="w-5 h-5 text-bambu-green" />
            ) : (
              <User className="w-5 h-5 text-bambu-green" />
            )
          }
          title={
            pickerMode === 'group'
              ? 'Select Groups / Cohorts'
              : 'Select Students / Users'
          }
          subtitle={
            <>
              Search and select one or more {pickerMode === 'group' ? 'groups' : 'students'} to grant access to{' '}
              <span className="text-white font-medium">{folder.name}</span>
            </>
          }
          subHeader={
            <div className="p-3 border-b border-bambu-dark-tertiary bg-bambu-dark/60 space-y-2">
              <div className="flex flex-col sm:flex-row gap-2">
                <div className="relative flex-1">
                  <Search className="w-4 h-4 text-bambu-gray absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    value={pickerSearch}
                    onChange={(e) => setPickerSearch(e.target.value)}
                    autoFocus
                    placeholder={
                      pickerMode === 'group'
                        ? 'Search groups by name or description...'
                        : 'Search students by name, username, email, or group...'
                    }
                    className="w-full pl-9 pr-3 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                  />
                </div>
                {pickerMode === 'user' && (data?.available_groups?.length ?? 0) > 0 && (
                  <select
                    value={pickerGroupFilter}
                    onChange={(e) => setPickerGroupFilter(e.target.value)}
                    aria-label="Filter by group"
                    className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-bambu-green sm:max-w-[190px]"
                  >
                    <option value="">All Groups / Cohorts</option>
                    {data?.available_groups.map((g) => (
                      <option key={g.id} value={g.name}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>

              <div className="flex items-center justify-between text-xs text-bambu-gray px-1">
                <span>
                  {pickerMode === 'group'
                    ? `${filteredPickerGroups.length} available group(s)`
                    : `${filteredPickerUsers.length} available student(s)`}
                  {selectedPickerIds.size > 0 && (
                    <span className="ml-2 text-bambu-green font-medium">
                      · {selectedPickerIds.size} selected
                    </span>
                  )}
                </span>
                <div className="flex items-center gap-3">
                  <button
                    type="button"
                    onClick={selectAllFiltered}
                    className="text-bambu-green hover:underline"
                  >
                    Select All Shown
                  </button>
                  {selectedPickerIds.size > 0 && (
                    <button
                      type="button"
                      onClick={() => setSelectedPickerIds(new Set())}
                      className="text-bambu-gray hover:text-white"
                    >
                      Clear
                    </button>
                  )}
                </div>
              </div>
            </div>
          }
          footer={
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-bambu-gray">Access Level:</span>
                <select
                  value={pickerAccessLevel}
                  onChange={(e) => setPickerAccessLevel(e.target.value as 'read' | 'write')}
                  aria-label="Access level for selected"
                  className="bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-bambu-green"
                >
                  <option value="write">Read & Write</option>
                  <option value="read">Read Only</option>
                </select>
              </div>
              <div className="flex justify-end gap-2">
                <Button type="button" variant="secondary" size="sm" onClick={closePicker}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="sm"
                  onClick={handleConfirmPicker}
                  disabled={selectedPickerIds.size === 0}
                >
                  <Plus className="w-4 h-4 mr-1" />
                  Add Selected ({selectedPickerIds.size})
                </Button>
              </div>
            </div>
          }
        >
          {pickerMode === 'group' ? (
            filteredPickerGroups.length === 0 ? (
              <div className="text-center py-10 text-sm text-bambu-gray">
                No matching groups found.
              </div>
            ) : (
              filteredPickerGroups.map((g) => {
                const checked = selectedPickerIds.has(g.id);
                return (
                  <label
                    key={g.id}
                    className={`flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors ${
                      checked
                        ? 'bg-bambu-green/10 border-bambu-green/50'
                        : 'bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-gray/50'
                    }`}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => togglePickerItem(g.id)}
                        className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green bg-bambu-dark-secondary"
                      />
                      <Users className="w-4 h-4 text-blue-400 flex-shrink-0" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-white truncate">
                          {g.name}
                        </div>
                        {g.description && (
                          <div className="text-xs text-bambu-gray truncate">
                            {g.description}
                          </div>
                        )}
                      </div>
                    </div>
                  </label>
                );
              })
            )
          ) : filteredPickerUsers.length === 0 ? (
            <div className="text-center py-10 text-sm text-bambu-gray">
              No matching students or users found.
            </div>
          ) : (
            filteredPickerUsers.map((u) => {
              const checked = selectedPickerIds.has(u.id);
              const fullName = [u.first_name, u.last_name].filter(Boolean).join(' ').trim();
              return (
                <label
                  key={u.id}
                  className={`flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors ${
                    checked
                      ? 'bg-bambu-green/10 border-bambu-green/50'
                      : 'bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-gray/50'
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => togglePickerItem(u.id)}
                      className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green bg-bambu-dark-secondary"
                    />
                    <User className="w-4 h-4 text-bambu-green flex-shrink-0" />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium text-white truncate">
                          {fullName || u.username}
                        </span>
                        {fullName && (
                          <span className="text-xs text-bambu-gray">
                            @{u.username}
                          </span>
                        )}
                      </div>
                      {u.email && (
                        <div className="text-xs text-bambu-gray truncate">
                          {u.email}
                        </div>
                      )}
                    </div>
                  </div>
                  {(u.group_names?.length ?? 0) > 0 && (
                    <div className="flex flex-wrap justify-end gap-1 max-w-[180px] flex-shrink-0">
                      {u.group_names!.slice(0, 2).map((gn) => (
                        <span
                          key={gn}
                          className="text-[10px] px-1.5 py-0.5 rounded bg-bambu-dark-secondary text-bambu-gray truncate max-w-[140px]"
                        >
                          {gn}
                        </span>
                      ))}
                      {u.group_names!.length > 2 && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-bambu-dark-secondary text-bambu-gray">
                          +{u.group_names!.length - 2}
                        </span>
                      )}
                    </div>
                  )}
                </label>
              );
            })
          )}
        </PopupModal>
      )}
    </>
  );
}
