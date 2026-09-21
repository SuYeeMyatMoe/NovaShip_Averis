import { redirect } from "next/navigation";

/** Bookmarks and Guide links still land on the run console. */
export default function AgentRedirect() {
  redirect("/workbench");
}
