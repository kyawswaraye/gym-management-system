import type { components } from "../types/api.generated";

import type { ApiClient } from "./client";
import type { SuccessEnvelope } from "./errors";
import type { CursorMeta } from "../pagination/cursor";

export type Schemas = components["schemas"];

export type Page<T> = {
  items: T[];
  meta: CursorMeta;
};

function pageOf<T>(envelope: SuccessEnvelope<T[]> | undefined): Page<T> {
  return {
    items: envelope?.data ?? [],
    meta: envelope?.meta ?? { has_more: false, next_after: null },
  };
}

export type MemberApi = {
  me(): Promise<Schemas["MemberSelfProfile"]>;
  updateMe(
    body: Schemas["PatchedMemberSelfUpdateRequest"],
  ): Promise<Schemas["MemberSelfProfile"]>;
  dashboard(): Promise<Schemas["MemberSelfDashboard"]>;
  membership(): Promise<Schemas["MemberSelfMembership"] | null>;
  payments(after?: string): Promise<Page<Schemas["MemberPaymentHistoryItem"]>>;
  checkIns(after?: string): Promise<Page<Schemas["CheckIn"]>>;
  servicePackages(after?: string): Promise<Page<Schemas["MemberSelfPackage"]>>;
  schedule(
    dateFrom: string,
    dateTo: string,
  ): Promise<Schemas["MemberSchedule"]>;
  bookClass(
    body: Schemas["MemberClassBookingWriteRequest"],
    idempotencyKey: string,
  ): Promise<Schemas["MemberScheduleBooking"]>;
  scan(payload: string): Promise<Schemas["CheckIn"]>;
  notifications(after?: string): Promise<Page<Schemas["MemberNotification"]>>;
  markNotificationRead(id: string): Promise<Schemas["MemberNotification"]>;
  registerDevice(
    body: Schemas["MemberDeviceWriteRequest"],
  ): Promise<Schemas["MemberDevice"]>;
  updateDevice(
    id: string,
    body: Schemas["PatchedMemberDevicePatchRequest"],
  ): Promise<Schemas["MemberDevice"]>;
  deleteDevice(id: string): Promise<void>;
  createPhotoIntent(
    body: Schemas["MemberProfilePhotoIntentRequest"],
  ): Promise<Schemas["UploadIntentData"]>;
  completePhoto(
    id: string,
    body: { sha256: string; byte_size: number },
  ): Promise<Schemas["CompletedFile"]>;
  photoDownloadUrl(id: string): Promise<Schemas["DownloadUrl"]>;
  deletePhoto(id: string): Promise<void>;
};

export function createMemberApi(client: ApiClient): MemberApi {
  return {
    async me() {
      const envelope =
        await client.request<Schemas["MemberSelfProfile"]>(
          "/api/v1/member/me/",
        );
      return required(envelope);
    },
    async updateMe(body) {
      const envelope = await client.request<Schemas["MemberSelfProfile"]>(
        "/api/v1/member/me/",
        {
          method: "PATCH",
          body,
        },
      );
      return required(envelope);
    },
    async dashboard() {
      const envelope = await client.request<Schemas["MemberSelfDashboard"]>(
        "/api/v1/member/dashboard/",
      );
      return required(envelope);
    },
    async membership() {
      const envelope = await client.request<
        Schemas["MemberSelfMembership"] | null
      >("/api/v1/member/membership/");
      return envelope ? envelope.data : null;
    },
    async payments(after) {
      const envelope = await client.request<
        Schemas["MemberPaymentHistoryItem"][]
      >("/api/v1/member/payments/", { query: { limit: 25, after } });
      return pageOf(envelope);
    },
    async checkIns(after) {
      const envelope = await client.request<Schemas["CheckIn"][]>(
        "/api/v1/member/check-ins/",
        {
          query: { limit: 25, after },
        },
      );
      return pageOf(envelope);
    },
    async servicePackages(after) {
      const envelope = await client.request<Schemas["MemberSelfPackage"][]>(
        "/api/v1/member/service-packages/",
        { query: { limit: 25, after } },
      );
      return pageOf(envelope);
    },
    async schedule(dateFrom, dateTo) {
      const envelope = await client.request<Schemas["MemberSchedule"]>(
        "/api/v1/member/schedule/",
        {
          query: { date_from: dateFrom, date_to: dateTo },
        },
      );
      return required(envelope);
    },
    async bookClass(body, idempotencyKey) {
      const envelope = await client.request<Schemas["MemberScheduleBooking"]>(
        "/api/v1/member/class-bookings/",
        { method: "POST", body, idempotencyKey },
      );
      return required(envelope);
    },
    async scan(payload) {
      const envelope = await client.request<Schemas["CheckIn"]>(
        "/api/v1/member/check-ins/scan/",
        {
          method: "POST",
          body: { payload },
        },
      );
      return required(envelope);
    },
    async notifications(after) {
      const envelope = await client.request<Schemas["MemberNotification"][]>(
        "/api/v1/member/notifications/",
        { query: { limit: 25, after } },
      );
      return pageOf(envelope);
    },
    async markNotificationRead(id) {
      const envelope = await client.request<Schemas["MemberNotification"]>(
        `/api/v1/member/notifications/${id}/read/`,
        { method: "POST", body: {} },
      );
      return required(envelope);
    },
    async registerDevice(body) {
      const response = await client.request<Schemas["MemberDevice"]>(
        "/api/v1/member/devices/",
        {
          method: "POST",
          body,
        },
      );
      return required(response);
    },
    async updateDevice(id, body) {
      const envelope = await client.request<Schemas["MemberDevice"]>(
        `/api/v1/member/devices/${id}/`,
        { method: "PATCH", body },
      );
      return required(envelope);
    },
    async deleteDevice(id) {
      await client.request(`/api/v1/member/devices/${id}/`, {
        method: "DELETE",
      });
    },
    async createPhotoIntent(body) {
      const envelope = await client.request<Schemas["UploadIntentData"]>(
        "/api/v1/member/files/upload-intents/",
        { method: "POST", body },
      );
      return required(envelope);
    },
    async completePhoto(id, body) {
      const envelope = await client.request<Schemas["CompletedFile"]>(
        `/api/v1/member/files/${id}/complete/`,
        { method: "POST", body },
      );
      return required(envelope);
    },
    async photoDownloadUrl(id) {
      const envelope = await client.request<Schemas["DownloadUrl"]>(
        `/api/v1/member/files/${id}/download-url/`,
      );
      return required(envelope);
    },
    async deletePhoto(id) {
      await client.request(`/api/v1/member/files/${id}/`, { method: "DELETE" });
    },
  };
}

function required<T>(envelope: SuccessEnvelope<T> | undefined): T {
  if (!envelope) {
    throw new Error("The server response could not be read.");
  }
  return envelope.data;
}
