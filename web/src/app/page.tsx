import { Suspense } from "react";
import Playground from "@/components/Playground";
import { Empty } from "@/components/ui";

export default function Page() {
  return (
    <Suspense fallback={<Empty>Loading playground…</Empty>}>
      <Playground />
    </Suspense>
  );
}
