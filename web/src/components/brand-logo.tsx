import Image from "next/image";

export function BrandLogo({
  align = "center",
}: {
  align?: "center" | "start";
}) {
  const centered = align === "center";

  return (
    <div
      className={
        centered
          ? "flex flex-col items-center text-center"
          : "flex items-center gap-3"
      }
    >
      <Image
        src="/brand/gympilot-mark.png"
        alt=""
        width={512}
        height={512}
        priority
        className={centered ? "h-[4.5rem] w-[4.5rem]" : "h-12 w-12"}
      />
      <p
        className={
          centered
            ? "mt-3 text-[0.95rem] leading-none font-semibold tracking-tight text-[#050505]"
            : "text-[0.95rem] leading-none font-semibold tracking-tight text-[#050505]"
        }
      >
        GymPilot
        <span className="ml-1 font-bold text-[#0866ff]">MM</span>
      </p>
    </div>
  );
}
