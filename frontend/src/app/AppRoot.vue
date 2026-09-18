<script setup lang="ts">
/**
 * Application root — a passthrough RouterView.
 *
 * The authenticated shell lives in AppLayout, which is mounted as a *layout
 * route* rather than the app root. Mounting AppLayout at the root would wrap
 * /login in the shell too, and since AppLayout calls getMe() on mount an
 * unauthenticated visitor would be bounced to /login from /login.
 */
</script>

<template>
  <RouterView />
</template>
