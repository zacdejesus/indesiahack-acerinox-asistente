// Service worker: guarda la "carcasa" de la app para que abra al instante y sin red.
// La API nunca se guarda en cache: stock y respuestas tienen que ser siempre frescos.
const VERSION = "inventario-v2";
const CARCASA = ["/", "/index.html", "/estilos.css", "/app.js", "/manifest.webmanifest",
                 "/iconos/icono-192.png", "/iconos/icono-512.png", "/iconos/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(CARCASA)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((claves) => Promise.all(claves.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  // Red primero: si hay conexion se ve la ultima version; si no, la guardada.
  e.respondWith(fetch(e.request)
    .then((r) => { const copia = r.clone(); caches.open(VERSION).then((c) => c.put(e.request, copia)); return r; })
    .catch(() => caches.match(e.request).then((r) => r || caches.match("/index.html"))));
});

// Al tocar una notificacion se abre (o se enfoca) la app.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type: "window"}).then((ventanas) =>
    ventanas.length ? ventanas[0].focus() : self.clients.openWindow("/")));
});

// Preparado para Web Push (VAPID). Falta la parte del servidor que envia los push.
self.addEventListener("push", (e) => {
  const datos = e.data ? e.data.json() : {titulo: "Inventario", texto: ""};
  e.waitUntil(self.registration.showNotification(datos.titulo, {body: datos.texto, icon: "/iconos/icono-192.png"}));
});
