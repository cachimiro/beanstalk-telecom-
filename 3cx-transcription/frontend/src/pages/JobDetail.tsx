import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { getJob, retryJob } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  unmatched: "bg-orange-100 text-orange-700",
  failed_parser: "bg-red-100 text-red-700",
  processing: "bg-yellow-100 text-yellow-700",
  transcribing: "bg-purple-100 text-purple-700",
  awaiting_callback: "bg-purple-100 text-purple-700",
  classifying_speakers: "bg-violet-100 text-violet-700",
  generating_subject: "bg-indigo-100 text-indigo-700",
  summarising: "bg-indigo-100 text-indigo-700",
  generating_transcript_html: "bg-sky-100 text-sky-700",
  emailing: "bg-cyan-100 text-cyan-700",
  queued: "bg-blue-100 text-blue-700",
};

const LOG_COLORS: Record<string, string> = {
  info: "text-gray-600",
  warning: "text-yellow-700",
  error: "text-red-600",
};

function Field({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div>
      <dt className="text-xs font-medium text-gray-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-gray-900">{value ?? "—"}</dd>
    </div>
  );
}

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: job, isLoading } = useQuery({
    queryKey: ["job", id],
    queryFn: () => getJob(id!).then((r) => r.data),
  });

  const retryMutation = useMutation({
    mutationFn: () => retryJob(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job", id] }),
  });

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>;
  if (!job) return <div className="p-6 text-gray-400">Job not found.</div>;

  const canRetry = ["failed", "unmatched", "failed_parser"].includes(job.status);

  return (
    <div className="p-6 max-w-4xl">
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-900 mb-6"
      >
        <ArrowLeft className="h-4 w-4" /> Back to Jobs
      </button>

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Job Detail</h1>
          <p className="text-xs text-gray-400 font-mono mt-0.5">{job.id}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className={`px-3 py-1 rounded-full text-sm font-medium ${STATUS_COLORS[job.status] || "bg-gray-100 text-gray-600"}`}>
            {job.status}
          </span>
          {canRetry && (
            <button
              onClick={() => retryMutation.mutate()}
              disabled={retryMutation.isPending}
              className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              <RefreshCw className="h-4 w-4" />
              {retryMutation.isPending ? "Retrying…" : "Retry"}
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6">
        {/* Recording info */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">Recording</h2>
          <dl className="grid grid-cols-2 gap-4">
            <Field label="GCS Object" value={job.gcs_object_name} />
            <Field label="Bucket" value={job.gcs_bucket} />
            <Field label="Generation" value={job.gcs_generation} />
            <Field label="File Size" value={job.file_size ? `${(job.file_size / 1024).toFixed(1)} KB` : null} />
          </dl>
        </div>

        {/* Parsed fields */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">Parsed Data</h2>
          <dl className="grid grid-cols-3 gap-4">
            <Field label="Extracted Name" value={job.extracted_name} />
            <Field label="Folder Extension" value={job.folder_extension} />
            <Field label="Filename Extension" value={job.filename_extension} />
            <Field label="Phone Number" value={job.phone_number} />
            <Field label="Call Timestamp" value={job.call_timestamp} />
            <Field label="Call ID" value={job.call_id} />
          </dl>
        </div>

        {/* Routing */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">Routing</h2>
          <dl className="grid grid-cols-2 gap-4">
            <Field label="Matched User" value={job.matched_user_name} />
            <Field label="Recipient Email" value={job.recipient_email} />
            <Field label="AssemblyAI Transcript ID" value={job.assemblyai_transcript_id} />
            <Field label="Email Summary MessageID" value={job.email_message_id} />
            <Field label="Email Transcript MessageID" value={job.email_transcript_message_id} />
            <Field label="Detected Language" value={job.detected_language} />
          </dl>
        </div>

        {/* Speaker Classification */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">Speaker Classification</h2>
          <dl className="grid grid-cols-2 gap-4">
            <div>
              <dt className="text-xs font-medium text-gray-500">Confidence Score</dt>
              <dd className="mt-0.5">
                {job.speaker_confidence_score != null ? (
                  <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                    job.speaker_confidence_score >= 0.75
                      ? "bg-green-100 text-green-700"
                      : "bg-orange-100 text-orange-700"
                  }`}>
                    {(job.speaker_confidence_score * 100).toFixed(0)}%
                    {job.speaker_confidence_score >= 0.75 ? " — Likely Agent/Customer labels used" : " — Neutral labels used"}
                  </span>
                ) : (
                  <span className="text-sm text-gray-400">—</span>
                )}
              </dd>
            </div>
            <div className="col-span-2">
              <dt className="text-xs font-medium text-gray-500">Classification Reason</dt>
              <dd className="mt-0.5 text-sm text-gray-700">{job.speaker_classification_reason || "—"}</dd>
            </div>
          </dl>
        </div>

        {/* Timing */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">Timing</h2>
          <dl className="grid grid-cols-2 gap-4">
            <Field label="Created" value={job.created_at ? new Date(job.created_at).toLocaleString() : null} />
            <Field label="Started" value={job.started_at ? new Date(job.started_at).toLocaleString() : null} />
            <Field label="Completed" value={job.completed_at ? new Date(job.completed_at).toLocaleString() : null} />
            <Field label="Emailed" value={job.emailed_at ? new Date(job.emailed_at).toLocaleString() : null} />
            <Field label="Retry Count" value={job.retry_count} />
          </dl>
        </div>

        {/* Error */}
        {job.error_message && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-red-800 mb-2">Error</h2>
            <p className="text-sm text-red-700 font-mono whitespace-pre-wrap">{job.error_message}</p>
          </div>
        )}

        {/* Logs */}
        <div className="bg-white border border-gray-200 rounded-lg p-5">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">Processing Logs</h2>
          {job.logs?.length === 0 ? (
            <p className="text-sm text-gray-400">No logs.</p>
          ) : (
            <div className="space-y-1 font-mono text-xs">
              {job.logs?.map((log: any) => (
                <div key={log.id} className="flex gap-3">
                  <span className="text-gray-400 whitespace-nowrap">
                    {new Date(log.created_at).toLocaleTimeString()}
                  </span>
                  <span className={`uppercase font-semibold w-12 shrink-0 ${LOG_COLORS[log.level] || "text-gray-600"}`}>
                    {log.level}
                  </span>
                  <span className="text-gray-700">{log.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
