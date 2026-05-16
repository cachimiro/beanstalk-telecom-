import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  withCredentials: true,
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

export default api;

// ── Auth ──────────────────────────────────────────────────────────────────────
export const login = (email: string, password: string) =>
  api.post("/auth/login", { email, password });

export const logout = () => api.post("/auth/logout");

export const getMe = () => api.get("/auth/me");

// ── Users ─────────────────────────────────────────────────────────────────────
export const getUsers = (params?: { search?: string; page?: number; page_size?: number }) =>
  api.get("/users", { params });

export const createUser = (data: { full_name: string; email: string; extension: string; active: boolean }) =>
  api.post("/users", data);

export const updateUser = (id: string, data: Partial<{ full_name: string; email: string; extension: string; active: boolean }>) =>
  api.put(`/users/${id}`, data);

export const toggleUser = (id: string) => api.patch(`/users/${id}/toggle`);

export const sendTestEmail = (id: string) => api.post(`/users/${id}/test-email`);

// ── Jobs ──────────────────────────────────────────────────────────────────────
export const getJobs = (params?: { status?: string; search?: string; page?: number; page_size?: number }) =>
  api.get("/jobs", { params });

export const getJob = (id: string) => api.get(`/jobs/${id}`);

export const retryJob = (id: string) => api.post(`/jobs/${id}/retry`);

// ── Settings ──────────────────────────────────────────────────────────────────
export const getSettings = () => api.get("/settings");

export const updateSettings = (data: Record<string, string>) =>
  api.put("/settings", data);
