# Candidato para la segunda prueba con hardware

Rama: `test/e2e2-hardware-20260919`. Se mantiene Studio 0.5.0-rc.1;
este es un build de prueba identificado por commit y SHA-256, sin nueva release.

El contrato `release-contract.json` fija el runtime, instalador, bootstrap y
los 104 archivos de KACE por identidad inmutable. El EXE debe construirse desde
esta revisión con Python 3.12.10 y PyInstaller 6.21.0, usando `main.spec`.
No reutilizar el EXE de la primera prueba, aunque muestre la misma versión.

El manifiesto del nuevo EXE registra commit, hashes, toolchain y firma. Un build
de prueba sin firma no constituye una release firmada ni certifica hardware.
Los checks de firma/publicación de releases siguen vigentes.

Seguir el recorrido de `KACE/docs/E2E2_HARDWARE_TRIAL.md` en la rama homónima:
SD limpia → imagen/verificación/expulsión → primer arranque → Discovery → SSH
→ bootstrap → firmware/MCU → configuración → Ready/COMPLETE.

Observar especialmente:

- Expulsión confirmada por Windows sin un falso error posterior.
- Discovery automático acotado y estado de energía pendiente antes del bootstrap.
- Progreso actualizado durante la misma sesión SSH; acciones acordes al estado.
- En la CLI ejecutada en la Pi, despliegue por Moonraker **127.0.0.1:7125**.
  La IP LAN se considera remota y sus escrituras se rechazan antes de la revisión.
- Hardware directamente en el `printer.cfg` nuevo, final localizado y separación
  clara entre instalación terminada y commissioning pendiente.
- Reintento sin cambios: verificar estado cargado sin reiniciar innecesariamente.

La prueba requiere acciones físicas del operador. La preparación de este build
no graba tarjetas, no conecta por SSH a la impresora ni ejecuta movimientos,
calentamiento o cambios del relé.
