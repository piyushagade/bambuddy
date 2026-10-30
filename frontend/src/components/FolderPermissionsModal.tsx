import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Shield, Users, User, Plus, Trash2, Loader2, X, Check } from 'lucide-react';
import { api } from '../api/client';
import type {
  LibraryFolderTree,
  FolderPermissionGrant,
} from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface FolderPermissionsModalProps {
  folder: LibraryFolderTree;
  onClose: () => void;
}

interface EditableGrant {
  key: string;
  user_id?: number | null;
  username?: string | null;
  group_id?: number | null;
  group_name?: string | null;
  can_read: boolean;
  can_write: boolean;
}

export function FolderPermissionsModal({ folder, onClose }: FolderPermissionsModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { data, isLoading } = useQuery({
    queryKey: ['folder-permissions', folder.id],
    queryFn: () => api.getLibraryFolderPermissions(folder.id),
  });

  const [grants, setGrants] = useState<EditableGrant[]>([]);
  const [targetType, setTargetType] = useState<'group' | 'user'>('group');
  const [selectedGroupId, setSelectedGroupId] = useState<string>('');
  const [selectedUserId, setSelectedUserId] = useState<string>('');
  const [newAccessLevel, setNewAccessLevel] = useState<'read' | 'write'>('write');

  useEffect(() => {
    if (data) {
      setGrants(
        data.permissions.map((p) => ({
          key: p.group_id ? `group:${p.group_id}` : `user:${p.user_id}`,
          user_id: p.user_id,
          username: p.username,
          group_id: p.group_id,
          group_name: p.group_name,
          can_read: p.can_read,
          can_write: p.can_write,
        })),
      );
    }
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: (payload: FolderPermissionGrant[]) =>
      api.updateLibraryFolderPermissions(folder.id, payload),
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

  const handleAddGrant = () => {
    if (targetType === 'group') {
      const gid = parseInt(selectedGroupId, 10);
      if (!gid) return;
      const groupObj = data?.available_groups.find((g) => g.id === gid);
      if (!groupObj) return;
      const key = `group:${gid}`;
      setGrants((prev) => {
        const filtered = prev.filter((item) => item.key !== key);
        return [
          ...filtered,
          {
            key,
            group_id: gid,
            group_name: groupObj.name,
            can_read: true,
            can_write: newAccessLevel === 'write',
          },
        ];
      });
      setSelectedGroupId('');
    } else {
      const uid = parseInt(selectedUserId, 10);
      if (!uid) return;
      const userObj = data?.available_users.find((u) => u.id === uid);
      if (!userObj) return;
      const key = `user:${uid}`;
      setGrants((prev) => {
        const filtered = prev.filter((item) => item.key !== key);
        return [
          ...filtered,
          {
            key,
            user_id: uid,
            username: userObj.username,
            can_read: true,
            can_write: newAccessLevel === 'write',
          },
        ];
      });
      setSelectedUserId('');
    }
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
    saveMutation.mutate(payload);
  };

  const grantedGroupIds = new Set(grants.filter((g) => g.group_id).map((g) => g.group_id!));
  const grantedUserIds = new Set(grants.filter((g) => g.user_id).map((g) => g.user_id!));

  const ungrantedGroups = (data?.available_groups ?? []).filter((g) => !grantedGroupIds.has(g.id));
  const ungrantedUsers = (data?.available_users ?? []).filter(
    (u) => !grantedUserIds.has(u.id) && u.id !== data?.created_by_id,
  );

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-lg border border-bambu-dark-tertiary flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <Shield className="w-5 h-5 text-bambu-green" />
              Folder Access & Sharing
            </h2>
            <p className="text-xs text-bambu-gray mt-0.5">
              Manage who can view or upload files in <span className="text-white font-medium">{folder.name}</span> (and its subfolders)
            </p>
          </div>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-4 overflow-y-auto flex-1">
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

              {/* Add Group / User form */}
              <div className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary space-y-3">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setTargetType('group')}
                    className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 px-3 rounded text-xs font-medium border transition-colors ${
                      targetType === 'group'
                        ? 'border-bambu-green bg-bambu-green/15 text-bambu-green'
                        : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white'
                    }`}
                  >
                    <Users className="w-3.5 h-3.5" />
                    Share with Group / Cohort
                  </button>
                  <button
                    type="button"
                    onClick={() => setTargetType('user')}
                    className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 px-3 rounded text-xs font-medium border transition-colors ${
                      targetType === 'user'
                        ? 'border-bambu-green bg-bambu-green/15 text-bambu-green'
                        : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white'
                    }`}
                  >
                    <User className="w-3.5 h-3.5" />
                    Share with Student / User
                  </button>
                </div>

                <div className="flex flex-col sm:flex-row gap-2">
                  {targetType === 'group' ? (
                    <select
                      value={selectedGroupId}
                      onChange={(e) => setSelectedGroupId(e.target.value)}
                      aria-label="Select group"
                      className="flex-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-bambu-green"
                    >
                      <option value="">Select a group...</option>
                      {ungrantedGroups.map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <select
                      value={selectedUserId}
                      onChange={(e) => setSelectedUserId(e.target.value)}
                      aria-label="Select user"
                      className="flex-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-bambu-green"
                    >
                      <option value="">Select a student / user...</option>
                      {ungrantedUsers.map((u) => (
                        <option key={u.id} value={u.id}>
                          {u.username}
                        </option>
                      ))}
                    </select>
                  )}

                  <select
                    value={newAccessLevel}
                    onChange={(e) => setNewAccessLevel(e.target.value as 'read' | 'write')}
                    aria-label="Access level"
                    className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-2.5 py-1.5 text-sm text-white focus:outline-none focus:border-bambu-green"
                  >
                    <option value="write">Read & Write</option>
                    <option value="read">Read Only</option>
                  </select>

                  <Button
                    type="button"
                    size="sm"
                    onClick={handleAddGrant}
                    disabled={targetType === 'group' ? !selectedGroupId : !selectedUserId}
                  >
                    <Plus className="w-4 h-4 mr-1" />
                    Add
                  </Button>
                </div>
              </div>

              {/* Current permissions list */}
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-bambu-gray mb-2">
                  Explicitly Granted Access ({grants.length})
                </h3>
                {grants.length === 0 ? (
                  <div className="text-center py-6 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary text-sm text-bambu-gray">
                    No groups or students have been granted explicit access to this folder yet.
                    <div className="text-xs mt-1 text-bambu-gray/70">
                      Only the folder owner, administrators, and anyone with access to a parent folder can access it.
                    </div>
                  </div>
                ) : (
                  <div className="space-y-1.5 max-h-60 overflow-y-auto">
                    {grants.map((g) => (
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
                            {g.group_id ? g.group_name : g.username}
                          </span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-bambu-dark-secondary text-bambu-gray">
                            {g.group_id ? 'Group' : 'User'}
                          </span>
                        </div>

                        <div className="flex items-center gap-2 flex-shrink-0">
                          <select
                            value={g.can_write ? 'write' : 'read'}
                            onChange={(e) =>
                              handleChangeLevel(g.key, e.target.value as 'read' | 'write')
                            }
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
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
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
      </div>
    </div>
  );
}
