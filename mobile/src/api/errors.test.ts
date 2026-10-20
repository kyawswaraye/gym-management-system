import { ApiError, parseErrorBody, parseSuccessBody } from "./errors";

describe("API envelopes", () => {
  it("reads a success envelope and cursor meta", () => {
    const envelope = parseSuccessBody<{ id: string }>(
      JSON.stringify({
        success: true,
        data: { id: "member" },
        meta: { has_more: true, next_after: "cursor-1" },
        message: "ok",
      }),
      200,
    );
    expect(envelope.data.id).toBe("member");
    expect(envelope.meta).toEqual({ has_more: true, next_after: "cursor-1" });
  });

  it("reads field errors and the request id", () => {
    const error = parseErrorBody(
      JSON.stringify({
        success: false,
        error: {
          code: "INVALID_REQUEST",
          message: "The request could not be processed.",
          fields: {
            email: ["Enter a valid email address."],
            address: { city: ["Required."] },
          },
        },
        request_id: "req-1",
      }),
      400,
      "2",
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("INVALID_REQUEST");
    expect(error.fields.email).toEqual(["Enter a valid email address."]);
    expect(error.fields["address.city"]).toEqual(["Required."]);
    expect(error.requestId).toBe("req-1");
    expect(error.retryAfter).toBe("2");
  });

  it("rejects a malformed success body", () => {
    expect(() => parseSuccessBody("not-json", 200)).toThrow(ApiError);
  });
});
