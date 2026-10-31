import type * as React from "react";
import { MessagesSquare } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";
import { ThreadList } from "@/components/assistant-ui/elements/thread-list.aui";

export function ThreadListSidebar({
  footer,
  ...props
}: React.ComponentProps<typeof Sidebar> & { footer?: React.ReactNode }) {
  return (
    <Sidebar {...props}>
      <SidebarHeader className="aui-sidebar-header mb-2 border-b">
        <SidebarMenu>
          <SidebarMenuItem className="flex items-center gap-2 p-2">
            <div className="aui-sidebar-header-icon-wrapper bg-sidebar-primary text-sidebar-primary-foreground flex aspect-square size-8 items-center justify-center rounded-lg">
              <MessagesSquare className="aui-sidebar-header-icon size-4" />
            </div>
            <h1 className="aui-sidebar-header-title font-semibold">
              Training AI
            </h1>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent className="aui-sidebar-content px-2">
        <ThreadList />
      </SidebarContent>
      {props.collapsible !== "none" && <SidebarRail />}
      {footer && (
        <SidebarFooter className="aui-sidebar-footer border-t">
          {footer}
        </SidebarFooter>
      )}
    </Sidebar>
  );
}
