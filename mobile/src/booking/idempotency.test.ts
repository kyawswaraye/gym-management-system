import { createIdempotencyKey } from "./idempotency";

describe("booking idempotency", () => {
  it("creates a key the class booking route accepts", () => {
    const key = createIdempotencyKey(
      () => "123e4567-e89b-42d3-a456-426614174000",
    );
    expect(key).toMatch(/^[A-Za-z0-9._:-]{16,128}$/);
  });

  it("rejects a short or unsafe key before it is sent", () => {
    expect(() => createIdempotencyKey(() => "short")).toThrow(/not valid/);
    expect(() => createIdempotencyKey(() => "bad key 1234567890")).toThrow(
      /not valid/,
    );
  });
});
