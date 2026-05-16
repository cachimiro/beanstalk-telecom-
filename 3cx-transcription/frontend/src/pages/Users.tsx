import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Search, Mail, ToggleLeft, ToggleRight, Pencil } from "lucide-react";
import { getUsers, createUser, updateUser, toggleUser, sendTestEmail } from "@/lib/api";

interface User {
  id: string;
  full_name: string;
  email: string;
  extension: string;
  active: boolean;
  created_at: string;
}

interface UserFormData {
  full_name: string;
  email: string;
  extension: string;
  active: boolean;
}

const emptyForm: UserFormData = { full_name: "", email: "", extension: "", active: true };

export default function Users() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [modal, setModal] = useState<{ open: boolean; user?: User }>({ open: false });
  const [form, setForm] = useState<UserFormData>(emptyForm);
  const [formError, setFormError] = useState("");
  const [toast, setToast] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["users", search, page],
    queryFn: () => getUsers({ search: search || undefined, page, page_size: 50 }).then((r) => r.data),
  });

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 3000);
  };

  const saveMutation = useMutation({
    mutationFn: (d: UserFormData) =>
      modal.user ? updateUser(modal.user.id, d) : createUser(d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      setModal({ open: false });
      showToast(modal.user ? "User updated." : "User created.");
    },
    onError: (err: any) => setFormError(err.response?.data?.detail || "Save failed."),
  });

  const toggleMutation = useMutation({
    mutationFn: (id: string) => toggleUser(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });

  const testEmailMutation = useMutation({
    mutationFn: (id: string) => sendTestEmail(id),
    onSuccess: () => showToast("Test email sent."),
    onError: (err: any) => showToast(err.response?.data?.detail || "Test email failed."),
  });

  const openCreate = () => {
    setForm(emptyForm);
    setFormError("");
    setModal({ open: true });
  };

  const openEdit = (user: User) => {
    setForm({ full_name: user.full_name, email: user.email, extension: user.extension, active: user.active });
    setFormError("");
    setModal({ open: true, user });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    saveMutation.mutate(form);
  };

  return (
    <div className="p-6">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 bg-gray-900 text-white text-sm px-4 py-2 rounded-md shadow-lg z-50">
          {toast}
        </div>
      )}

      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold text-gray-900">Users</h1>
        <button
          onClick={openCreate}
          className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          <Plus className="h-4 w-4" /> Add User
        </button>
      </div>

      {/* Search */}
      <div className="relative mb-4 max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
        <input
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          placeholder="Search name, email, extension…"
          className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              {["Full Name", "Email", "Extension", "Status", "Created", "Actions"].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">Loading…</td></tr>
            ) : data?.items?.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">No users found.</td></tr>
            ) : (
              data?.items?.map((user: User) => (
                <tr key={user.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-medium text-gray-900">{user.full_name}</td>
                  <td className="px-4 py-3 text-gray-600">{user.email}</td>
                  <td className="px-4 py-3">
                    <span className="font-mono bg-gray-100 px-2 py-0.5 rounded text-xs">{user.extension}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                      user.active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                    }`}>
                      {user.active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {new Date(user.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => openEdit(user)}
                        title="Edit"
                        className="p-1 text-gray-400 hover:text-blue-600 transition-colors"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => toggleMutation.mutate(user.id)}
                        title={user.active ? "Deactivate" : "Activate"}
                        className="p-1 text-gray-400 hover:text-blue-600 transition-colors"
                      >
                        {user.active ? <ToggleRight className="h-4 w-4 text-green-500" /> : <ToggleLeft className="h-4 w-4" />}
                      </button>
                      <button
                        onClick={() => testEmailMutation.mutate(user.id)}
                        title="Send test email"
                        className="p-1 text-gray-400 hover:text-blue-600 transition-colors"
                      >
                        <Mail className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        {data && data.total > 50 && (
          <div className="px-4 py-3 border-t border-gray-200 flex items-center justify-between text-sm text-gray-500">
            <span>Showing {(page - 1) * 50 + 1}–{Math.min(page * 50, data.total)} of {data.total}</span>
            <div className="flex gap-2">
              <button disabled={page === 1} onClick={() => setPage(p => p - 1)} className="px-3 py-1 border rounded disabled:opacity-40">Prev</button>
              <button disabled={page * 50 >= data.total} onClick={() => setPage(p => p + 1)} className="px-3 py-1 border rounded disabled:opacity-40">Next</button>
            </div>
          </div>
        )}
      </div>

      {/* Modal */}
      {modal.open && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-40">
          <div className="bg-white rounded-lg shadow-xl p-6 w-full max-w-md">
            <h2 className="text-base font-semibold text-gray-900 mb-4">
              {modal.user ? "Edit User" : "Add User"}
            </h2>
            <form onSubmit={handleSubmit} className="space-y-4">
              {(["full_name", "email", "extension"] as const).map((field) => (
                <div key={field}>
                  <label className="block text-xs font-medium text-gray-700 mb-1 capitalize">
                    {field.replace("_", " ")}
                  </label>
                  <input
                    value={form[field]}
                    onChange={(e) => setForm({ ...form, [field]: e.target.value })}
                    required
                    className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              ))}
              <div className="flex items-center gap-3">
                <label className="text-xs font-medium text-gray-700">Active</label>
                <button
                  type="button"
                  onClick={() => setForm({ ...form, active: !form.active })}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${form.active ? "bg-blue-600" : "bg-gray-300"}`}
                >
                  <span className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${form.active ? "translate-x-5" : "translate-x-1"}`} />
                </button>
              </div>
              {formError && <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{formError}</p>}
              <div className="flex gap-3 pt-2">
                <button type="submit" disabled={saveMutation.isPending} className="flex-1 bg-blue-600 text-white rounded-md py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50">
                  {saveMutation.isPending ? "Saving…" : "Save"}
                </button>
                <button type="button" onClick={() => setModal({ open: false })} className="flex-1 border border-gray-300 rounded-md py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
