ENTRY(start)
SECTIONS
{
    . = 0x02038010;
    .image : {
        *(.text.start)
        *(.text .text.*)
        *(.rodata .rodata.*)
        *(.data .data.*)
        *(.bss .bss.* COMMON)
        . = ALIGN(4);
    }
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
