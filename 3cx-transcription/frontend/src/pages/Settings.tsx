import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getSettings, updateSettings } from "@/lib/api";

export default function Settings() {
  const qc = useQueryClient();
  const [toast, setToast] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => getSettings().then((r) => r.data),
  });

  const [form, setForm] = useState<Record<string, string>>({});

  const saveMutation = useMutation({
    mutationFn: (d: Record<string, string>) => updateSettings(d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      setToast("Settings saved.");
      setTimeout(() => setToast(""), 3000);
    },
  });

  const dbSettings = data?.db_settings || {};
  const envInfo = data?.env_info || {};

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    saveMutation.mutate(form);
  };

  const val = (key: string) => (key in form ? form[key] : dbSettings[key] ?? "");

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>;

  return (
    <div className="p-6 max-w-3xl">
      {toast && (
        <div className="fixed top-4 right-4 bg-gray-900 text-white text-sm px-4 py-2 rounded-md shadow-lg z-50">
          {toast}
        </div>
      )}

      <h1 className="text-xl font-semibold text-gray-900 mb-6">Settings</h1>

      {/* Editable DB settings */}
      <div className="bg-white border border-gray-200 rounded-lg p-5 mb-6">
        <h2 className="text-sm font-semibold text-gray-900 mb-4">Configuration</h2>
        <form onSubmit={handleSave} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">Admin Email</label>
              <input
                value={val("admin_email")}
                onChange={(e) => setForm({ ...form, admin_email: e.target.value })}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="admin@example.com"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1">Max Retries</label>
              <input
                type="number"
                min={1}
                max={10}
                value={val("max_retries")}
                onChange={(e) => setForm({ ...form, max_retries: e.target.value })}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">Default Email Subject</label>
            <input
              value={val("default_email_subject")}
              onChange={(e) => setForm({ ...form, default_email_subject: e.target.value })}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex gap-6">
            {[
              { key: "store_transcripts", label: "Store Transcripts" },
              { key: "debug_mode", label: "Debug Mode" },
            ].map(({ key, label }) => (
              <div key={key} className="flex items-center gap-3">
                <label className="text-xs font-medium text-gray-700">{label}</label>
                <button
                  type="button"
                  onClick={() => setForm({ ...form, [key]: val(key) === "true" ? "false" : "true" })}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${val(key) === "true" ? "bg-blue-600" : "bg-gray-300"}`}
                >
                  <span className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${val(key) === "true" ? "translate-x-5" : "translate-x-1"}`} />
                </button>
              </div>
            ))}
          </div>
          <button
            type="submit"
            disabled={saveMutation.isPending}
            className="bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {saveMutation.isPending ? "Saving…" : "Save Settings"}
          </button>
        </form>
      </div>

      {/* Read-only env info */}
      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h2 className="text-sm font-semibold text-gray-900 mb-1">Environment</h2>
        <p className="text-xs text-gray-500 mb-4">API keys are masked. Change them by updating the .env file on the server.</p>
        <dl className="grid grid-cols-2 gap-3">
          {Object.entries(envInfo).map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs font-medium text-gray-500">{key}</dt>
              <dd className="mt-0.5 text-sm text-gray-900 font-mono break-all">{String(value)}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
