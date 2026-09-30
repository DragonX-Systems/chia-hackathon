OUTPUT_ARCH(riscv) /* MediumBOOM adapter testbench */
ENTRY(_start)
MEMORY { TCM : ORIGIN = 0x00000000, LENGTH = 64K }
SECTIONS {
  .text : { *(.text*) } > TCM
  .rodata : { *(.rodata*) } > TCM
  .data : { *(.data*) } > TCM
  .bss : { *(.bss*) *(COMMON) } > TCM
  . = 0xF000;
  .tohost : { KEEP(*(.tohost)) } > TCM
}
