import { useState, useMemo } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Users,
  Shield,
  Search,
  Plus,
  Edit2,
  Trash2,
  UploadCloud,
  ExternalLink,
  Loader2,
  Calendar,
  UserPlus,
  UserMinus,
  Power,
  PowerOff,
  AlertTriangle,
} from 'lucide-react';
import { api } from '../api/client';
import type { UserResponse, Group } from '../api/client';
import { Button } from './Button';
import { PopupModal } from './PopupModal';
import { UserExpirationBadge, datetimeLocalToIso } from './UserExpirationField';
import { useToast } from '../contexts/ToastContext';

interface UsersManagerModalProps {
  users: UserResponse[];
  groups: Group[];
  currentUserId?: number | null;
  canCreate: boolean;
  canUpdate: boolean;
  canDelete: boolean;
  onClose: () => void;
  onAddUser: () => void;
  onImportRoster: () => void;
  onEnrollmentInvites: () => void;
  onEditUser: (user: UserResponse) => void;
  onDeleteUser: (userId: number) => void;
}

export function UsersManagerModal({
  users,
  groups,
  currentUserId,
  canCreate,
  canUpdate,
  canDelete,
  onClose,
  onAddUser,
  onImportRoster,
  onEnrollmentInvites,
  onEditUser,
  onDeleteUser,
}: UsersManagerModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [search, setSearch] = useState('');
  const [groupFilter, setGroupFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<'username' | 'name' | 'expires' | 'newest'>('username');

  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // Bulk action panel states
  const [bulkActionTab, setBulkActionTab] = useState<
    'none' | 'add_group' | 'remove_group' | 'expiration' | 'delete'
  >('none');
  const [bulkGroupId, setBulkGroupId] = useState<string>('');
  const [bulkExpiresAt, setBulkExpiresAt] = useState<string>('');
  const [bulkDeleteItems, setBulkDeleteItems] = useState<boolean>(false);

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = users.filter((u) => {
      if (groupFilter === 'ungrouped') {
        if ((u.groups?.length ?? 0) > 0) return false;
      } else if (groupFilter !== 'all') {
        const gid = Number(groupFilter);
        if (!u.groups?.some((g) => g.id === gid)) return false;
      }

      if (statusFilter === 'active' && (!u.is_active || u.is_expired)) return false;
      if (statusFilter === 'expired' && u.is_active && !u.is_expired) return false;
      if (statusFilter === 'has_expiry' && !u.expires_at) return false;
      if (statusFilter === 'no_expiry' && u.expires_at) return false;

      if (!q) return true;
      const fullName = [u.first_name, u.last_name].filter(Boolean).join(' ').toLowerCase();
      const reverseName = [u.last_name, u.first_name].filter(Boolean).join(', ').toLowerCase();
      return (
        u.username.toLowerCase().includes(q) ||
        fullName.includes(q) ||
        reverseName.includes(q) ||
        (u.email && u.email.toLowerCase().includes(q)) ||
        (u.groups ?? []).some((g) => g.name.toLowerCase().includes(q))
      );
    });

    return [...list].sort((a, b) => {
      if (sortBy === 'name') {
        const nameA = [a.last_name, a.first_name].filter(Boolean).join(' ') || a.username;
        const nameB = [b.last_name, b.first_name].filter(Boolean).join(' ') || b.username;
        return nameA.localeCompare(nameB);
      }
      if (sortBy === 'expires') {
        if (!a.expires_at && !b.expires_at) return a.username.localeCompare(b.username);
        if (!a.expires_at) return 1;
        if (!b.expires_at) return -1;
        return a.expires_at.localeCompare(b.expires_at);
      }
      if (sortBy === 'newest') {
        return b.id - a.id;
      }
      return a.username.localeCompare(b.username);
    });
  }, [users, search, groupFilter, statusFilter, sortBy]);

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllFiltered = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const u of filteredUsers) {
        next.add(u.id);
      }
      return next;
    });
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setBulkActionTab('none');
  };

  const bulkMutation = useMutation({
    mutationFn: async (action: {
      kind: 'add_group' | 'remove_group' | 'set_expiry' | 'clear_expiry' | 'activate' | 'deactivate' | 'delete';
      groupId?: number;
      expiresAt?: string | null;
      deleteItems?: boolean;
    }) => {
      const ids = Array.from(selectedIds);
      let succeeded = 0;
      let failed = 0;
      let lastError = '';

      for (const uid of ids) {
        try {
          if (action.kind === 'add_group' && action.groupId) {
            const targetUser = users.find((u) => u.id === uid);
            if (targetUser && !targetUser.groups?.some((g) => g.id === action.groupId)) {
              await api.addUserToGroup(action.groupId, uid);
            }
            succeeded++;
          } else if (action.kind === 'remove_group' && action.groupId) {
            const targetUser = users.find((u) => u.id === uid);
            if (targetUser && targetUser.groups?.some((g) => g.id === action.groupId)) {
              await api.removeUserFromGroup(action.groupId, uid);
            }
            succeeded++;
          } else if (action.kind === 'set_expiry') {
            await api.updateUser(uid, { expires_at: action.expiresAt });
            succeeded++;
          } else if (action.kind === 'clear_expiry') {
            await api.updateUser(uid, { expires_at: null });
            succeeded++;
          } else if (action.kind === 'activate') {
            await api.updateUser(uid, { is_active: true });
            succeeded++;
          } else if (action.kind === 'deactivate') {
            if (uid === currentUserId) {
              failed++;
              lastError = 'Skipped current logged-in user';
              continue;
            }
            await api.updateUser(uid, { is_active: false });
            succeeded++;
          } else if (action.kind === 'delete') {
            if (uid === currentUserId) {
              failed++;
              lastError = 'Skipped current logged-in user';
              continue;
            }
            await api.deleteUser(uid, Boolean(action.deleteItems));
            succeeded++;
          }
        } catch (err) {
          failed++;
          lastError = err instanceof Error ? err.message : 'Operation failed';
        }
      }

      return { succeeded, failed, lastError };
    },
    onSuccess: ({ succeeded, failed, lastError }) => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      if (failed === 0) {
        showToast(`Bulk action applied to ${succeeded} user(s)`, 'success');
        clearSelection();
      } else {
        showToast(
          `Completed: ${succeeded} succeeded, ${failed} skipped/failed (${lastError})`,
          succeeded > 0 ? 'success' : 'error',
        );
        setBulkActionTab('none');
      }
    },
    onError: (err: Error) => {
      showToast(err.message || 'Bulk action failed', 'error');
    },
  });

  const setRelativeSemesterPreset = (days: number) => {
    const dt = new Date(Date.now() + days * 24 * 60 * 60 * 1000);
    const pad = (n: number) => String(n).padStart(2, '0');
    setBulkExpiresAt(`${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T23:59`);
  };

  return (
    <PopupModal
      onClose={onClose}
      maxWidth="5xl"
      bodyClassName="p-3 space-y-1.5"
      icon={
        <div className="w-9 h-9 rounded-lg bg-bambu-green/15 flex items-center justify-center">
          <Users className="w-5 h-5 text-bambu-green" />
        </div>
      }
      title={
        <>
          Users Directory & Bulk Manager
          <span className="text-xs px-2 py-0.5 rounded-full bg-bambu-dark text-bambu-gray font-normal">
            {users.length} total
          </span>
        </>
      }
      subtitle="Filter by cohort or status, select multiple students, and apply bulk group, expiration, or account actions"
      headerActions={
        canCreate ? (
          <>
            <Button size="sm" variant="secondary" onClick={onImportRoster}>
              <UploadCloud className="w-4 h-4" />
              Import Roster CSV
            </Button>
            <Button size="sm" variant="secondary" onClick={onEnrollmentInvites}>
              <ExternalLink className="w-4 h-4" />
              Enrollment Invites
            </Button>
            <Button size="sm" onClick={onAddUser}>
              <Plus className="w-4 h-4" />
              Add User
            </Button>
          </>
        ) : undefined
      }
      subHeader={
        <>
          {/* Filter & Search Toolbar */}
          <div className="p-3 border-b border-bambu-dark-tertiary bg-bambu-dark/60 space-y-2.5 flex-shrink-0">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2">
            <div className="relative lg:col-span-2">
              <Search className="w-4 h-4 text-bambu-gray absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                autoFocus
                placeholder="Search by username, student name, email, or group..."
                className="w-full pl-9 pr-3 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              />
            </div>

            <select
              value={groupFilter}
              onChange={(e) => setGroupFilter(e.target.value)}
              aria-label="Filter by group"
              className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-bambu-green"
            >
              <option value="all">All Groups / Cohorts ({users.length})</option>
              <option value="ungrouped">Ungrouped Users</option>
              {groups.map((g) => (
                <option key={g.id} value={String(g.id)}>
                  {g.name} ({g.user_count})
                </option>
              ))}
            </select>

            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              aria-label="Filter by status"
              className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-bambu-green"
            >
              <option value="all">All Statuses</option>
              <option value="active">Active Only</option>
              <option value="expired">Expired / Inactive</option>
              <option value="has_expiry">Has Expiration Date</option>
              <option value="no_expiry">No Expiration Date</option>
            </select>

            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
              aria-label="Sort users"
              className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-bambu-green"
            >
              <option value="username">Sort: Username (A–Z)</option>
              <option value="name">Sort: Last / First Name</option>
              <option value="expires">Sort: Expiration Date</option>
              <option value="newest">Sort: Newest Added</option>
            </select>
          </div>

          {/* Selection Counter & Quick Select Controls */}
          <div className="flex items-center justify-between flex-wrap gap-2 text-xs text-bambu-gray px-1">
            <div className="flex items-center gap-3">
              <span>
                Showing <strong className="text-white">{filteredUsers.length}</strong> of{' '}
                {users.length} user(s)
              </span>
              {selectedIds.size > 0 && (
                <span className="px-2 py-0.5 rounded bg-bambu-green/20 text-bambu-green font-medium">
                  {selectedIds.size} selected
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={selectAllFiltered}
                className="text-bambu-green hover:underline font-medium"
              >
                Select All Shown ({filteredUsers.length})
              </button>
              {selectedIds.size > 0 && (
                <button
                  type="button"
                  onClick={clearSelection}
                  className="text-bambu-gray hover:text-white"
                >
                  Clear Selection
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Bulk Actions Bar (visible when users are selected) */}
        {selectedIds.size > 0 && (canUpdate || canDelete) && (
          <div className="p-3 border-b border-bambu-green/30 bg-bambu-green/5 space-y-2.5 flex-shrink-0">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-xs font-medium text-white">
                Bulk Actions for {selectedIds.size} selected user(s):
              </span>
              <div className="flex items-center gap-1.5 flex-wrap">
                {canUpdate && (
                  <>
                    <Button
                      size="sm"
                      variant={bulkActionTab === 'add_group' ? 'primary' : 'secondary'}
                      onClick={() =>
                        setBulkActionTab(bulkActionTab === 'add_group' ? 'none' : 'add_group')
                      }
                    >
                      <UserPlus className="w-3.5 h-3.5" />
                      Add to Group
                    </Button>
                    <Button
                      size="sm"
                      variant={bulkActionTab === 'remove_group' ? 'primary' : 'secondary'}
                      onClick={() =>
                        setBulkActionTab(
                          bulkActionTab === 'remove_group' ? 'none' : 'remove_group',
                        )
                      }
                    >
                      <UserMinus className="w-3.5 h-3.5" />
                      Remove from Group
                    </Button>
                    <Button
                      size="sm"
                      variant={bulkActionTab === 'expiration' ? 'primary' : 'secondary'}
                      onClick={() =>
                        setBulkActionTab(bulkActionTab === 'expiration' ? 'none' : 'expiration')
                      }
                    >
                      <Calendar className="w-3.5 h-3.5" />
                      Set / Clear Expiry
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={bulkMutation.isPending}
                      onClick={() => bulkMutation.mutate({ kind: 'activate' })}
                    >
                      <Power className="w-3.5 h-3.5 text-green-400" />
                      Activate
                    </Button>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={bulkMutation.isPending}
                      onClick={() => bulkMutation.mutate({ kind: 'deactivate' })}
                    >
                      <PowerOff className="w-3.5 h-3.5 text-amber-400" />
                      Deactivate
                    </Button>
                  </>
                )}
                {canDelete && (
                  <Button
                    size="sm"
                    variant={bulkActionTab === 'delete' ? 'primary' : 'secondary'}
                    onClick={() =>
                      setBulkActionTab(bulkActionTab === 'delete' ? 'none' : 'delete')
                    }
                    className="text-red-400 hover:text-red-300"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    Delete ({selectedIds.size})
                  </Button>
                )}
              </div>
            </div>

            {/* Sub-panel: Add / Remove Group */}
            {(bulkActionTab === 'add_group' || bulkActionTab === 'remove_group') && (
              <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-bambu-dark-tertiary">
                <span className="text-xs text-bambu-gray">
                  {bulkActionTab === 'add_group'
                    ? 'Assign selected users to group:'
                    : 'Remove selected users from group:'}
                </span>
                <select
                  value={bulkGroupId}
                  onChange={(e) => setBulkGroupId(e.target.value)}
                  aria-label="Target group"
                  className="bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-bambu-green"
                >
                  <option value="">Select a group...</option>
                  {groups.map((g) => (
                    <option key={g.id} value={String(g.id)}>
                      {g.name}
                    </option>
                  ))}
                </select>
                <Button
                  size="sm"
                  disabled={!bulkGroupId || bulkMutation.isPending}
                  onClick={() =>
                    bulkMutation.mutate({
                      kind: bulkActionTab === 'add_group' ? 'add_group' : 'remove_group',
                      groupId: Number(bulkGroupId),
                    })
                  }
                >
                  {bulkMutation.isPending ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    'Apply'
                  )}
                </Button>
              </div>
            )}

            {/* Sub-panel: Set / Clear Expiration */}
            {bulkActionTab === 'expiration' && (
              <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-bambu-dark-tertiary">
                <span className="text-xs text-bambu-gray">Expiration date:</span>
                <input
                  type="datetime-local"
                  value={bulkExpiresAt}
                  onChange={(e) => setBulkExpiresAt(e.target.value)}
                  className="bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-bambu-green"
                />
                <button
                  type="button"
                  onClick={() => setRelativeSemesterPreset(120)}
                  className="px-2 py-1 text-xs rounded bg-bambu-dark hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white border border-bambu-dark-tertiary"
                >
                  +1 Semester (120d)
                </button>
                <Button
                  size="sm"
                  disabled={!bulkExpiresAt || bulkMutation.isPending}
                  onClick={() =>
                    bulkMutation.mutate({
                      kind: 'set_expiry',
                      expiresAt: datetimeLocalToIso(bulkExpiresAt),
                    })
                  }
                >
                  {bulkMutation.isPending ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    'Set Expiration'
                  )}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={bulkMutation.isPending}
                  onClick={() => bulkMutation.mutate({ kind: 'clear_expiry' })}
                >
                  Clear Expiration (Never)
                </Button>
              </div>
            )}

            {/* Sub-panel: Bulk Delete Confirmation */}
            {bulkActionTab === 'delete' && (
              <div className="flex items-center justify-between gap-3 flex-wrap pt-2 border-t border-red-500/30 bg-red-500/10 p-2.5 rounded-lg">
                <div className="flex items-center gap-3 flex-wrap">
                  <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />
                  <span className="text-xs text-red-200 font-medium">
                    Permanently delete {selectedIds.size} selected user(s)?
                  </span>
                  <label className="flex items-center gap-1.5 text-xs text-bambu-gray cursor-pointer">
                    <input
                      type="checkbox"
                      checked={bulkDeleteItems}
                      onChange={(e) => setBulkDeleteItems(e.target.checked)}
                      className="w-3.5 h-3.5 rounded border-bambu-gray text-red-500 bg-bambu-dark"
                    />
                    Also delete archives, queue items & library files created by these users
                  </label>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setBulkActionTab('none')}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    disabled={bulkMutation.isPending}
                    onClick={() =>
                      bulkMutation.mutate({
                        kind: 'delete',
                        deleteItems: bulkDeleteItems,
                      })
                    }
                    className="bg-red-600 hover:bg-red-500 text-white"
                  >
                    {bulkMutation.isPending ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      `Confirm Delete (${selectedIds.size})`
                    )}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
        </>
      }
      footer={
        <div className="flex items-center justify-between">
          <span className="text-xs text-bambu-gray">
            Tip: Click any row to toggle selection for bulk actions.
          </span>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      {/* Scrollable Users Table / List */}
      {filteredUsers.length === 0 ? (
        <div className="text-center py-12 text-sm text-bambu-gray">
          No users match the current search or filter criteria.
        </div>
      ) : (
        filteredUsers.map((userItem) => {
          const checked = selectedIds.has(userItem.id);
          const fullName = [userItem.first_name, userItem.last_name]
            .filter(Boolean)
            .join(' ')
            .trim();
          return (
            <div
              key={userItem.id}
              onClick={() => toggleSelect(userItem.id)}
              className={`flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors ${
                checked
                  ? 'bg-bambu-green/10 border-bambu-green/50'
                  : 'bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-gray/50'
              }`}
            >
              <div className="flex items-center gap-3 min-w-0 flex-1">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleSelect(userItem.id)}
                  onClick={(e) => e.stopPropagation()}
                  className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green bg-bambu-dark-secondary flex-shrink-0"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-sm font-medium text-white truncate">
                      {userItem.username}
                    </span>
                    {fullName && (
                      <span className="text-xs text-bambu-gray truncate">
                        ({fullName})
                      </span>
                    )}
                    {userItem.email && (
                      <span className="text-xs text-bambu-gray/70 truncate hidden sm:inline">
                        · {userItem.email}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {userItem.auth_source === 'ldap' && (
                      <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-cyan-500/20 text-cyan-300">
                        LDAP
                      </span>
                    )}
                    {userItem.is_admin && (
                      <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-purple-500/20 text-purple-300">
                        Admin
                      </span>
                    )}
                    {userItem.groups?.map((group) => (
                      <span
                        key={group.id}
                        className={`px-2 py-0.5 rounded-full text-[11px] font-medium ${
                          group.name === 'Administrators'
                            ? 'bg-purple-500/20 text-purple-300'
                            : group.name === 'Operators'
                            ? 'bg-blue-500/20 text-blue-300'
                            : group.name === 'Viewers'
                            ? 'bg-green-500/20 text-green-300'
                            : 'bg-gray-500/20 text-gray-300'
                        }`}
                      >
                        {group.name}
                      </span>
                    ))}
                    <UserExpirationBadge user={userItem} />
                  </div>
                </div>
              </div>

              <div
                className="flex items-center gap-1 flex-shrink-0"
                onClick={(e) => e.stopPropagation()}
              >
                {canUpdate && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onEditUser(userItem)}
                    title="Edit user"
                  >
                    <Edit2 className="w-4 h-4" />
                  </Button>
                )}
                {canDelete && userItem.id !== currentUserId && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onDeleteUser(userItem.id)}
                    title="Delete user"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                )}
              </div>
            </div>
          );
        })
      )}
    </PopupModal>
  );
}

interface GroupsManagerModalProps {
  groups: Group[];
  canCreate: boolean;
  canUpdate: boolean;
  canDelete: boolean;
  onClose: () => void;
  onAddGroup: () => void;
  onEditGroup: (groupId: number) => void;
  onDeleteGroup: (groupId: number) => void;
}

export function GroupsManagerModal({
  groups,
  canCreate,
  canUpdate,
  canDelete,
  onClose,
  onAddGroup,
  onEditGroup,
  onDeleteGroup,
}: GroupsManagerModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<'all' | 'custom' | 'system' | 'active' | 'expired'>('all');
  const [sortBy, setSortBy] = useState<'name' | 'members' | 'expires'>('name');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  const [bulkTab, setBulkTab] = useState<'none' | 'expiration' | 'delete'>('none');
  const [bulkExpiresAt, setBulkExpiresAt] = useState<string>('');
  const [bulkDeleteUsers, setBulkDeleteUsers] = useState(false);
  const [bulkDeleteUserItems, setBulkDeleteUserItems] = useState(false);

  const filteredGroups = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = groups.filter((g) => {
      if (typeFilter === 'custom' && g.is_system) return false;
      if (typeFilter === 'system' && !g.is_system) return false;
      if (typeFilter === 'active' && g.is_expired) return false;
      if (typeFilter === 'expired' && !g.is_expired) return false;

      if (!q) return true;
      return (
        g.name.toLowerCase().includes(q) ||
        (g.description && g.description.toLowerCase().includes(q))
      );
    });

    return [...list].sort((a, b) => {
      if (sortBy === 'members') {
        return b.user_count - a.user_count;
      }
      if (sortBy === 'expires') {
        if (!a.expires_at && !b.expires_at) return a.name.localeCompare(b.name);
        if (!a.expires_at) return 1;
        if (!b.expires_at) return -1;
        return a.expires_at.localeCompare(b.expires_at);
      }
      return a.name.localeCompare(b.name);
    });
  }, [groups, search, typeFilter, sortBy]);

  const toggleSelect = (group: Group) => {
    if (group.is_system) return; // System groups are protected from bulk modification/deletion
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(group.id)) next.delete(group.id);
      else next.add(group.id);
      return next;
    });
  };

  const selectAllCustomShown = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const g of filteredGroups) {
        if (!g.is_system) next.add(g.id);
      }
      return next;
    });
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setBulkTab('none');
    setBulkDeleteUsers(false);
    setBulkDeleteUserItems(false);
  };

  const bulkGroupMutation = useMutation({
    mutationFn: async (action: {
      kind: 'set_expiry' | 'clear_expiry' | 'delete';
      expiresAt?: string | null;
      deleteUsers?: boolean;
      deleteUserItems?: boolean;
    }) => {
      const ids = Array.from(selectedIds);
      let succeeded = 0;
      let failed = 0;
      let lastError = '';

      for (const gid of ids) {
        try {
          if (action.kind === 'set_expiry') {
            await api.updateGroup(gid, { expires_at: action.expiresAt });
            succeeded++;
          } else if (action.kind === 'clear_expiry') {
            await api.updateGroup(gid, { expires_at: null });
            succeeded++;
          } else if (action.kind === 'delete') {
            await api.deleteGroup(gid, {
              deleteUsers: action.deleteUsers,
              deleteUserItems: action.deleteUsers && action.deleteUserItems,
            });
            succeeded++;
          }
        } catch (err) {
          failed++;
          lastError = err instanceof Error ? err.message : 'Operation failed';
        }
      }

      return { succeeded, failed, lastError };
    },
    onSuccess: ({ succeeded, failed, lastError }) => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      queryClient.invalidateQueries({ queryKey: ['users'] });
      if (failed === 0) {
        showToast(`Bulk action applied to ${succeeded} group(s)`, 'success');
        clearSelection();
      } else {
        showToast(
          `Completed: ${succeeded} succeeded, ${failed} failed (${lastError})`,
          succeeded > 0 ? 'success' : 'error',
        );
        setBulkTab('none');
      }
    },
    onError: (err: Error) => {
      showToast(err.message || 'Bulk group action failed', 'error');
    },
  });

  return (
    <PopupModal
      onClose={onClose}
      maxWidth="4xl"
      bodyClassName="p-3 space-y-1.5"
      icon={
        <div className="w-9 h-9 rounded-lg bg-bambu-green/15 flex items-center justify-center">
          <Shield className="w-5 h-5 text-bambu-green" />
        </div>
      }
      title={
        <>
          Groups & Course Cohorts Manager
          <span className="text-xs px-2 py-0.5 rounded-full bg-bambu-dark text-bambu-gray font-normal">
            {groups.length} total
          </span>
        </>
      }
      subtitle="Filter course cohorts and groups, select multiple custom groups, and manage expiration or cleanup in bulk"
      headerActions={
        canCreate ? (
          <Button size="sm" onClick={onAddGroup}>
            <Plus className="w-4 h-4" />
            Add Group
          </Button>
        ) : undefined
      }
      subHeader={
        <>
          {/* Filter & Search Bar */}
          <div className="p-3 border-b border-bambu-dark-tertiary bg-bambu-dark/60 space-y-2.5 flex-shrink-0">
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
              <div className="relative sm:col-span-2">
                <Search className="w-4 h-4 text-bambu-gray absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  autoFocus
                  placeholder="Search groups by name (e.g. F26 - ABE4042C) or description..."
                  className="w-full pl-9 pr-3 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                />
              </div>

              <select
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value as typeof typeFilter)}
                aria-label="Filter groups"
                className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-bambu-green"
              >
                <option value="all">All Groups ({groups.length})</option>
                <option value="custom">Custom / Cohort Groups</option>
                <option value="system">System Groups</option>
                <option value="active">Active Only</option>
                <option value="expired">Expired Only</option>
              </select>

              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
                aria-label="Sort groups"
                className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-bambu-green"
              >
                <option value="name">Sort: Name (A–Z)</option>
                <option value="members">Sort: Member Count</option>
                <option value="expires">Sort: Expiration Date</option>
              </select>
            </div>

            <div className="flex items-center justify-between flex-wrap gap-2 text-xs text-bambu-gray px-1">
              <div className="flex items-center gap-3">
                <span>
                  Showing <strong className="text-white">{filteredGroups.length}</strong> of{' '}
                  {groups.length} group(s)
                </span>
                {selectedIds.size > 0 && (
                  <span className="px-2 py-0.5 rounded bg-bambu-green/20 text-bambu-green font-medium">
                    {selectedIds.size} custom group(s) selected
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={selectAllCustomShown}
                  className="text-bambu-green hover:underline font-medium"
                >
                  Select All Custom Shown
                </button>
                {selectedIds.size > 0 && (
                  <button
                    type="button"
                    onClick={clearSelection}
                    className="text-bambu-gray hover:text-white"
                  >
                    Clear Selection
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Bulk Actions Bar */}
          {selectedIds.size > 0 && (canUpdate || canDelete) && (
            <div className="p-3 border-b border-bambu-green/30 bg-bambu-green/5 space-y-2.5 flex-shrink-0">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <span className="text-xs font-medium text-white">
                  Bulk Actions for {selectedIds.size} selected group(s):
                </span>
                <div className="flex items-center gap-2">
                  {canUpdate && (
                    <Button
                      size="sm"
                      variant={bulkTab === 'expiration' ? 'primary' : 'secondary'}
                      onClick={() =>
                        setBulkTab(bulkTab === 'expiration' ? 'none' : 'expiration')
                      }
                    >
                      <Calendar className="w-3.5 h-3.5" />
                      Set / Clear Cohort Expiry
                    </Button>
                  )}
                  {canDelete && (
                    <Button
                      size="sm"
                      variant={bulkTab === 'delete' ? 'primary' : 'secondary'}
                      onClick={() => setBulkTab(bulkTab === 'delete' ? 'none' : 'delete')}
                      className="text-red-400 hover:text-red-300"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Delete ({selectedIds.size})
                    </Button>
                  )}
                </div>
              </div>

              {bulkTab === 'expiration' && (
                <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-bambu-dark-tertiary">
                  <span className="text-xs text-bambu-gray">Cohort expiration date:</span>
                  <input
                    type="datetime-local"
                    value={bulkExpiresAt}
                    onChange={(e) => setBulkExpiresAt(e.target.value)}
                    className="bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-bambu-green"
                  />
                  <Button
                    size="sm"
                    disabled={!bulkExpiresAt || bulkGroupMutation.isPending}
                    onClick={() =>
                      bulkGroupMutation.mutate({
                        kind: 'set_expiry',
                        expiresAt: datetimeLocalToIso(bulkExpiresAt),
                      })
                    }
                  >
                    {bulkGroupMutation.isPending ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      'Set Expiration'
                    )}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={bulkGroupMutation.isPending}
                    onClick={() => bulkGroupMutation.mutate({ kind: 'clear_expiry' })}
                  >
                    Clear Expiration (Never)
                  </Button>
                </div>
              )}

              {bulkTab === 'delete' && (
                <div className="space-y-2.5 pt-2 border-t border-red-500/30 bg-red-500/10 p-2.5 rounded-lg">
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />
                      <span className="text-xs text-red-200 font-medium">
                        Delete {selectedIds.size} selected custom group(s)?
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Button size="sm" variant="secondary" onClick={() => setBulkTab('none')}>
                        Cancel
                      </Button>
                      <Button
                        size="sm"
                        disabled={bulkGroupMutation.isPending}
                        onClick={() =>
                          bulkGroupMutation.mutate({
                            kind: 'delete',
                            deleteUsers: bulkDeleteUsers,
                            deleteUserItems: bulkDeleteUsers && bulkDeleteUserItems,
                          })
                        }
                        className="bg-red-600 hover:bg-red-500 text-white"
                      >
                        {bulkGroupMutation.isPending ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          `Confirm Delete (${selectedIds.size})`
                        )}
                      </Button>
                    </div>
                  </div>

                  <div className="space-y-1.5 pl-6">
                    <label className="flex items-start gap-2 text-xs text-white cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={bulkDeleteUsers}
                        onChange={(e) => {
                          setBulkDeleteUsers(e.target.checked);
                          if (!e.target.checked) setBulkDeleteUserItems(false);
                        }}
                        className="mt-0.5 rounded border-bambu-dark-tertiary text-red-500 focus:ring-red-500"
                      />
                      <span>
                        <strong>Also delete users who belong solely to each deleted group</strong>{' '}
                        <span className="text-bambu-gray">
                          (Users in other groups and admins are kept)
                        </span>
                      </span>
                    </label>

                    {bulkDeleteUsers && (
                      <label className="flex items-start gap-2 text-xs text-bambu-gray hover:text-white cursor-pointer select-none pl-5">
                        <input
                          type="checkbox"
                          checked={bulkDeleteUserItems}
                          onChange={(e) => setBulkDeleteUserItems(e.target.checked)}
                          className="mt-0.5 rounded border-bambu-dark-tertiary text-red-500 focus:ring-red-500"
                        />
                        <span>
                          Also delete print archives, queue items & library files created by those users
                        </span>
                      </label>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      }
      footer={
        <div className="flex items-center justify-between">
          <span className="text-xs text-bambu-gray">
            System groups (Administrators, Operators, Viewers) are protected from bulk deletion.
          </span>
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      }
    >
      {/* Scrollable Groups List */}
      {filteredGroups.length === 0 ? (
        <div className="text-center py-12 text-sm text-bambu-gray">
          No groups match the current search or filter criteria.
        </div>
      ) : (
        filteredGroups.map((group) => {
          const checked = selectedIds.has(group.id);
          return (
            <div
              key={group.id}
              onClick={() => toggleSelect(group)}
              className={`flex items-center justify-between gap-3 px-3 py-3 rounded-lg border transition-colors ${
                group.is_system ? 'cursor-default' : 'cursor-pointer'
              } ${
                checked
                  ? 'bg-bambu-green/10 border-bambu-green/50'
                  : 'bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-gray/50'
              }`}
            >
              <div className="flex items-start gap-3 min-w-0 flex-1">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={group.is_system}
                  onChange={() => toggleSelect(group)}
                  onClick={(e) => e.stopPropagation()}
                  title={
                    group.is_system
                      ? 'System groups cannot be bulk modified or deleted'
                      : 'Select group'
                  }
                  className="mt-1 w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green bg-bambu-dark-secondary disabled:opacity-30 flex-shrink-0"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Shield
                      className={`w-4 h-4 flex-shrink-0 ${
                        group.name === 'Administrators'
                          ? 'text-purple-400'
                          : group.name === 'Operators'
                          ? 'text-blue-400'
                          : group.name === 'Viewers'
                          ? 'text-green-400'
                          : 'text-bambu-gray'
                      }`}
                    />
                    <span className="text-white font-medium text-sm">{group.name}</span>
                    {group.is_system && (
                      <span className="px-2 py-0.5 rounded text-[11px] bg-yellow-500/20 text-yellow-400">
                        System
                      </span>
                    )}
                    <UserExpirationBadge user={group} />
                  </div>
                  <p className="text-xs text-bambu-gray mt-1">
                    {group.description || 'No description'}
                  </p>
                  <div className="flex items-center gap-4 mt-1.5 text-xs text-bambu-gray">
                    <span>{group.user_count} user(s)</span>
                    <span>{group.permissions.length} permission(s)</span>
                  </div>
                </div>
              </div>

              <div
                className="flex items-center gap-1 flex-shrink-0"
                onClick={(e) => e.stopPropagation()}
              >
                {canUpdate && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onEditGroup(group.id)}
                    title="Edit group"
                  >
                    <Edit2 className="w-4 h-4" />
                  </Button>
                )}
                {canDelete && !group.is_system && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => onDeleteGroup(group.id)}
                    title="Delete group"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                )}
              </div>
            </div>
          );
        })
      )}
    </PopupModal>
  );
}
