import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Upload, Link2, Plus, Trash2, Copy, Check, X, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import type { Group, RosterImportResponse } from '../api/client';
import { Button } from './Button';
import { Card, CardContent, CardHeader } from './Card';
import { UserExpirationField, datetimeLocalToIso } from './UserExpirationField';
import { useToast } from '../contexts/ToastContext';

interface RosterImportModalProps {
  groups: Group[];
  onClose: () => void;
}

export function RosterImportModal({ groups, onClose }: RosterImportModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [csvContent, setCsvContent] = useState('');
  const [defaultGroupId, setDefaultGroupId] = useState<string>('');
  const [defaultExpiresAt, setDefaultExpiresAt] = useState<string>('');
  const [result, setResult] = useState<RosterImportResponse | null>(null);

  const importMutation = useMutation({
    mutationFn: () =>
      api.importRosterCsv({
        csv_content: csvContent,
        default_group_id: defaultGroupId ? Number(defaultGroupId) : null,
        default_expires_at: datetimeLocalToIso(defaultExpiresAt),
      }),
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      showToast(
        `Roster imported: ${data.created_count} created, ${data.updated_count} updated`,
        'success'
      );
    },
    onError: (err: Error) => {
      showToast(err.message, 'error');
    },
  });

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setCsvContent(reader.result);
      }
    };
    reader.readAsText(file);
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <Card
        className="w-full max-w-xl max-h-[90vh] flex flex-col"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        <CardHeader className="flex-shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Upload className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">
                Bulk CSV Roster Import (Canvas / UF Registrar)
              </h2>
            </div>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 overflow-y-auto flex-1">
          <p className="text-xs text-bambu-gray">
            Upload a <strong className="text-white">UF Class Roll CSV</strong> (
            <code className="text-white">UFID,Last Name,First Name,Email,Subject,Catalog Number,Term,...</code>
            ) or a custom CSV (
            <code className="text-white">username,email,group,cost_center,expires_at</code>). Each
            student&apos;s <strong className="text-white">username</strong> is set to the prefix of their{' '}
            <code className="text-white">Email</code> before <code className="text-white">@</code>, their
            initial <strong className="text-white">password</strong> is set to their{' '}
            <code className="text-white">UFID</code>, and their course group (e.g.{' '}
            <code className="text-white">F26 - ABE4042C</code>) and semester expiration are created
            automatically.
          </p>

          <div>
            <label className="block text-xs font-medium text-bambu-gray mb-1">
              Upload CSV File
            </label>
            <input
              type="file"
              accept=".csv,text/csv,text/plain"
              onChange={handleFileUpload}
              className="block w-full text-xs text-bambu-gray file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-medium file:bg-bambu-dark-tertiary file:text-white hover:file:bg-bambu-green/20"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-white mb-1">
              CSV Content
            </label>
            <textarea
              rows={6}
              value={csvContent}
              onChange={(e) => setCsvContent(e.target.value)}
              placeholder={
                'username,email,group,cost_center,expires_at\n' +
                'albert.gator,albert@ufl.edu,EML2322L-Fall2026,EML2322L,2026-12-15T23:59:00Z'
              }
              className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-xs font-mono text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-white mb-1">
              Default Fallback Group (when CSV row omits group)
            </label>
            <select
              value={defaultGroupId}
              onChange={(e) => setDefaultGroupId(e.target.value)}
              className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-sm text-white"
            >
              <option value="">Operators (default)</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </div>

          <UserExpirationField
            value={defaultExpiresAt}
            onChange={setDefaultExpiresAt}
          />

          {result && (
            <div className="p-3 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary text-xs space-y-1">
              <div className="text-white font-medium">
                Created: {result.created_count} · Updated: {result.updated_count} · Skipped:{' '}
                {result.skipped_count}
              </div>
              {result.errors.length > 0 && (
                <ul className="text-red-400 list-disc list-inside">
                  {result.errors.map((err, i) => (
                    <li key={i}>{err}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

        </CardContent>
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-bambu-dark-tertiary flex-shrink-0">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button
            onClick={() => importMutation.mutate()}
            disabled={importMutation.isPending || !csvContent.trim()}
          >
            {importMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Importing...
              </>
            ) : (
              <>
                <Upload className="w-4 h-4" />
                Import Roster
              </>
            )}
          </Button>
        </div>
      </Card>
    </div>
  );
}

interface EnrollmentInvitesModalProps {
  groups: Group[];
  onClose: () => void;
}

export function EnrollmentInvitesModal({ groups, onClose }: EnrollmentInvitesModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [groupId, setGroupId] = useState<string>('');
  const [maxUses, setMaxUses] = useState<string>('');
  const [inviteExpiresAt, setInviteExpiresAt] = useState<string>('');
  const [userExpiresAt, setUserExpiresAt] = useState<string>('');
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const setInviteRelativeDays = (days: number) => {
    const dt = new Date(Date.now() + days * 24 * 60 * 60 * 1000);
    const pad = (n: number) => String(n).padStart(2, '0');
    setInviteExpiresAt(
      `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T23:59`
    );
  };

  const { data: invites = [], isLoading } = useQuery({
    queryKey: ['enrollmentInvites'],
    queryFn: () => api.getEnrollmentInvites(),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.createEnrollmentInvite({
        name,
        code: code.trim() || null,
        group_id: groupId ? Number(groupId) : null,
        max_uses: maxUses ? Number(maxUses) : null,
        invite_expires_at: datetimeLocalToIso(inviteExpiresAt),
        user_expires_at: datetimeLocalToIso(userExpiresAt),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['enrollmentInvites'] });
      setName('');
      setCode('');
      setMaxUses('');
      setInviteExpiresAt('');
      showToast('Enrollment invite code created', 'success');
    },
    onError: (err: Error) => {
      showToast(err.message, 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteEnrollmentInvite(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['enrollmentInvites'] });
      showToast('Invite code deleted', 'success');
    },
  });

  const copyCode = (id: number, inviteCode: string) => {
    navigator.clipboard?.writeText(inviteCode);
    setCopiedId(id);
    showToast(`Copied code: ${inviteCode}`, 'success');
    setTimeout(() => setCopiedId(null), 1500);
  };

  const copyLink = (id: number, inviteCode: string) => {
    const url = `${window.location.origin}/login?invite=${encodeURIComponent(inviteCode)}`;
    navigator.clipboard?.writeText(url);
    setCopiedId(id);
    showToast('Copied self-enrollment link to clipboard', 'success');
    setTimeout(() => setCopiedId(null), 1500);
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <Card
        className="w-full max-w-2xl max-h-[90vh] flex flex-col"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        <CardHeader className="flex-shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Link2 className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">
                Self-Service Enrollment Codes
              </h2>
            </div>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 overflow-y-auto flex-1">
          <div className="p-3 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary space-y-3">
            <h3 className="text-sm font-medium text-white">Create Course / Cohort Invite Code</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-bambu-gray mb-1">Course / Cohort Label *</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="EML2322L Fall 2026"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white"
                />
              </div>
              <div>
                <label className="block text-xs text-bambu-gray mb-1">Custom Code (optional)</label>
                <input
                  type="text"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="UF-EML2322L-F26"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white font-mono"
                />
              </div>
              <div>
                <label className="block text-xs text-bambu-gray mb-1">Target Group</label>
                <select
                  value={groupId}
                  onChange={(e) => setGroupId(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white"
                >
                  <option value="">None</option>
                  {groups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-bambu-gray mb-1">
                  Max Uses / Redemptions (optional)
                </label>
                <input
                  type="number"
                  min={1}
                  value={maxUses}
                  onChange={(e) => setMaxUses(e.target.value)}
                  placeholder="Unlimited (e.g. 35 for class size)"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white"
                />
              </div>
              <div className="md:col-span-2">
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs text-bambu-gray">
                    Invite Code Expiration Datetime (optional — when this code stops working)
                  </label>
                  {inviteExpiresAt && (
                    <button
                      type="button"
                      onClick={() => setInviteExpiresAt('')}
                      className="text-xs text-bambu-gray hover:text-white underline"
                    >
                      Never expires / Clear
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    type="datetime-local"
                    value={inviteExpiresAt}
                    onChange={(e) => setInviteExpiresAt(e.target.value)}
                    className="flex-1 min-w-[200px] px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-white"
                  />
                  <div className="flex flex-wrap gap-1.5">
                    <button
                      type="button"
                      onClick={() => setInviteRelativeDays(1)}
                      className="px-2 py-1 text-xs rounded bg-bambu-dark border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-green/50"
                    >
                      +24 Hours
                    </button>
                    <button
                      type="button"
                      onClick={() => setInviteRelativeDays(7)}
                      className="px-2 py-1 text-xs rounded bg-bambu-dark border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-green/50"
                    >
                      +7 Days (Drop/Add)
                    </button>
                    <button
                      type="button"
                      onClick={() => setInviteRelativeDays(14)}
                      className="px-2 py-1 text-xs rounded bg-bambu-dark border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-green/50"
                    >
                      +14 Days
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <div className="pt-1 border-t border-bambu-dark-tertiary">
              <div className="text-xs text-bambu-gray mb-1 font-medium">
                Enrolled Student Account Expiration (when student printer access ends):
              </div>
              <UserExpirationField
                value={userExpiresAt}
                onChange={setUserExpiresAt}
              />
            </div>
            <div className="flex justify-end">
              <Button
                size="sm"
                onClick={() => createMutation.mutate()}
                disabled={createMutation.isPending || !name.trim()}
              >
                <Plus className="w-4 h-4" />
                Mint Invite Code
              </Button>
            </div>
          </div>

          <div className="space-y-2">
            <h3 className="text-sm font-medium text-white">Active Invite Codes</h3>
            {isLoading ? (
              <div className="flex justify-center py-4">
                <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
              </div>
            ) : invites.length === 0 ? (
              <p className="text-xs text-bambu-gray py-3 text-center">No enrollment codes created yet.</p>
            ) : (
              <div className="divide-y divide-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg">
                {invites.map((inv) => (
                  <div key={inv.id} className="p-3 flex items-center justify-between gap-2 text-xs">
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono font-semibold text-bambu-green">{inv.code}</span>
                        <span className="text-white font-medium">{inv.name}</span>
                        {inv.group_name && (
                          <span className="px-2 py-0.5 rounded bg-blue-500/20 text-blue-300">
                            {inv.group_name}
                          </span>
                        )}
                        {inv.is_redeemable ? (
                          <span className="px-2 py-0.5 rounded bg-green-500/20 text-green-300">
                            Active
                          </span>
                        ) : (
                          <span className="px-2 py-0.5 rounded bg-red-500/20 text-red-300">
                            {inv.max_uses && inv.uses_count >= inv.max_uses
                              ? 'Max Uses Reached'
                              : 'Invite Expired'}
                          </span>
                        )}
                      </div>
                      <div className="text-bambu-gray mt-1">
                        Uses: {inv.uses_count}
                        {inv.max_uses ? ` / ${inv.max_uses}` : ' (unlimited)'}
                        {inv.invite_expires_at &&
                          ` · Code expires: ${inv.invite_expires_at.replace('T', ' ').slice(0, 16)}`}
                        {inv.user_expires_at &&
                          ` · Student access expires: ${inv.user_expires_at.slice(0, 10)}`}
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => copyLink(inv.id, inv.code)}
                        title="Copy direct student enrollment link (/login?invite=...)"
                      >
                        <Link2 className="w-4 h-4 text-bambu-green" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => copyCode(inv.id, inv.code)}
                        title="Copy invite code"
                      >
                        {copiedId === inv.id ? <Check className="w-4 h-4 text-bambu-green" /> : <Copy className="w-4 h-4" />}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => deleteMutation.mutate(inv.id)}
                        title="Revoke invite"
                      >
                        <Trash2 className="w-4 h-4 text-red-400" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </CardContent>
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-bambu-dark-tertiary flex-shrink-0">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </Card>
    </div>
  );
}

interface RedeemInviteModalProps {
  initialCode?: string;
  isAuthenticated?: boolean;
  onSuccess?: (resp: { access_token?: string | null; user: import('../api/client').UserResponse }) => void;
  onClose: () => void;
}

export function RedeemInviteModal({
  initialCode = '',
  isAuthenticated = false,
  onSuccess,
  onClose,
}: RedeemInviteModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [code, setCode] = useState(initialCode);
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const handleEmailChange = (val: string) => {
    setEmail(val);
    if (val.includes('@') && !username) {
      setUsername(val.split('@')[0]);
    }
  };

  const redeemMutation = useMutation({
    mutationFn: () =>
      api.redeemEnrollmentInvite({
        code: code.trim(),
        ...(isAuthenticated
          ? {}
          : {
              username: username.trim() || (email.includes('@') ? email.split('@')[0] : ''),
              password,
              email: email.trim() || null,
            }),
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      showToast('Course invite redeemed!', 'success');
      onSuccess?.(data);
      onClose();
    },
    onError: (err: Error) => {
      showToast(err.message, 'error');
    },
  });

  const canSubmit = isAuthenticated
    ? Boolean(code.trim())
    : Boolean(code.trim() && (username.trim() || email.includes('@')) && password);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <Card
        className="w-full max-w-md max-h-[90vh] flex flex-col"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        <CardHeader className="flex-shrink-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Link2 className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">
                {isAuthenticated ? 'Redeem Course Invite Code' : 'Student Course Self-Enrollment'}
              </h2>
            </div>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 overflow-y-auto flex-1">
          <p className="text-xs text-bambu-gray">
            {isAuthenticated
              ? 'Enter the course invite code provided by your instructor or TA to join the course group and extend your semester access.'
              : 'Enter the course invite code from your syllabus/Canvas to create your student printer farm account and join your course group.'}
          </p>

          <div>
            <label className="block text-xs font-medium text-white mb-1">
              Course Invite Code
            </label>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="e.g. UF-ABE4042C-FALL26"
              className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-sm font-mono text-white"
              autoFocus
            />
          </div>

          {!isAuthenticated && (
            <>
              <div>
                <label className="block text-xs font-medium text-white mb-1">
                  UF Email Address
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => handleEmailChange(e.target.value)}
                  placeholder="gator@ufl.edu"
                  className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-sm text-white"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-white mb-1">
                  Username (GatorLink / Email Prefix)
                </label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="gator"
                  className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-sm text-white"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-white mb-1">
                  Password (or UFID)
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Create a password"
                  className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-sm text-white"
                />
              </div>
            </>
          )}
        </CardContent>
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-bambu-dark-tertiary flex-shrink-0">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => redeemMutation.mutate()}
            disabled={redeemMutation.isPending || !canSubmit}
          >
            {redeemMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Enrolling...
              </>
            ) : (
              'Enroll in Course'
            )}
          </Button>
        </div>
      </Card>
    </div>
  );
}
