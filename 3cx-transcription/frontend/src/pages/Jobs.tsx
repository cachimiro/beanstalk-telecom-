import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Search, RefreshCw } from "lucide-react";
import { getJobs, retryJob } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  received: "bg-gray-100 text-gray-600",
  queued: "bg-blue-100 text-blue-700",
  processing: "bg-yellow-100 text-yellow-700",
  transcribing: "bg-purple-100 text-purple-700",
  awaiting_callback: "bg-purple-100 text-purple-700",
  classifying_speakers: "bg-violet-100 text-violet-700",
  generating_subject: "bg-indigo-100 text-indigo-700",
  summarising: "bg-indigo-100 text-indigo-700",
  generating_transcript_html: "bg-sky-100 text-sky-700",
  emailing: "bg-cyan-100 text-cyan-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  unmatched: "bg-orange-100 text-orange-700",
  failed_parser: "bg-red-100 text-red-700",
  ignored: "bg-gray-100 text-gray-400",
};

const ALL_STATUSES = [
  "received", "queued", "processing", "transcribing", "awaiting_callback",
  "classifying_speakers", "generating_subject", "summarising", "generating_transcript_html",
  "emailing", "completed", "failed", "unmatched", "failed_parser", "ignored",
];

export default function Jobs() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [toast, setToast] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["jobs", search, statusFilter, page],
    queryFn: () =>
      getJobs({ search: search || undefined, status: statusFilter || undefined, page, page_size: 50 }).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const retryMutation = useMutation({
    mutationFn: (id: string) => retryJob(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setToast("Job re-queued.");
      setTimeout(() => setToast(""), 3000);
    },
  });

  const canRetry = (status: string) => ["failed", "unmatched", "failed_parser"].includes(status);

  return (
    <div className="p-6">
      {toast && (
        <div className="fixed top-4 right-4 bg-gray-900 text-white text-sm px-4 py-2 rounded-md shadow-lg z-50">
          {toast}
        </div>
      )}

      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold text-gray-900">Jobs</h1>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ["jobs"] })}
          className="flex items-center gap-2 border border-gray-300 px-3 py-2 rounded-md text-sm text-gray-600 hover:bg-gray-50"
        >
          <RefreshCw className="h-4 w-4" /> Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-4">
        <div className="relative max-w-xs flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            placeholder="Search file, name, phone…"
            className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">All statuses</option>
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              {["Date", "File", "Name", "Ext", "Matched User", "Recipient", "Status", "Error", "Actions"].map((h) => (
                <th key={h} className="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading ? (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-gray-400">Loading…</td></tr>
            ) : data?.items?.length === 0 ? (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-gray-400">No jobs found.</td></tr>
            ) : (
              data?.items?.map((job: any) => (
                <tr
                  key={job.id}
                  className="hover:bg-gray-50 cursor-pointer"
                  onClick={() => navigate(`/admin/jobs/${job.id}`)}
                >
                  <td className="px-3 py-3 text-gray-500 text-xs whitespace-nowrap">
                    {job.created_at ? new Date(job.created_at).toLocaleString() : "—"}
                  </td>
                  <td className="px-3 py-3 max-w-[180px]">
                    <span className="truncate block text-xs text-gray-600 font-mono" title={job.gcs_object_name}>
                      {job.gcs_object_name?.split("/").pop() || "—"}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-gray-700">{job.extracted_name || "—"}</td>
                  <td className="px-3 py-3">
                    <span className="font-mono bg-gray-100 px-1.5 py-0.5 rounded text-xs">{job.folder_extension || "—"}</span>
                  </td>
                  <td className="px-3 py-3 text-gray-700">{job.matched_user_name || "—"}</td>
                  <td className="px-3 py-3 text-gray-600 text-xs">{job.recipient_email || "—"}</td>
                  <td className="px-3 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[job.status] || "bg-gray-100 text-gray-600"}`}>
                      {job.status}
                    </span>
                  </td>
                  <td className="px-3 py-3 max-w-[140px]">
                    {job.error_message ? (
                      <span className="text-xs text-red-600 truncate block" title={job.error_message}>
                        {job.error_message}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                    {canRetry(job.status) && (
                      <button
                        onClick={() => retryMutation.mutate(job.id)}
                        disabled={retryMutation.isPending}
                        className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
                      >
                        <RefreshCw className="h-3 w-3" /> Retry
                      </button>
                    )}
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
    </div>
  );
}
