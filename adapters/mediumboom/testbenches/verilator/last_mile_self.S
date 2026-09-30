    .section .text  # MediumBOOM directed last-mile stimulus
    .globl _start
_start:
    la   x10, scratch
    li   x5,  0xAABBCCDD
    sw   x5,  0(x10)
    sw   x5,  4(x10)
    sw   x5,  8(x10)
    sw   x5,  12(x10)

    # --- ALU add/sub/logic (hits ALU_ADD/SUB/AND/OR/XOR) ---
    li   x5,  7
    li   x6,  3
    add  x7,  x5, x6
    sub  x7,  x5, x6
    and  x7,  x5, x6
    or   x7,  x5, x6
    xor  x7,  x5, x6
    addi x7,  x5, 9
    andi x7,  x5, 5
    ori  x7,  x5, 8
    xori x7,  x5, 1
    lui  x7,  0x12

    # --- Shift-left every shamt bit (ALU_SHIFTL if/else lanes) ---
    li   x5,  0x1
    li   x6,  0
    sll  x7,  x5, x6
    li   x6,  1
    sll  x7,  x5, x6
    slli x7,  x5, 1
    li   x6,  2
    sll  x7,  x5, x6
    slli x7,  x5, 2
    li   x6,  4
    sll  x7,  x5, x6
    slli x7,  x5, 4
    li   x6,  8
    sll  x7,  x5, x6
    slli x7,  x5, 8
    li   x6,  16
    sll  x7,  x5, x6
    slli x7,  x5, 16
    li   x6,  31
    sll  x7,  x5, x6

    # --- Logical shift-right every shamt bit ---
    li   x5,  0x80000000
    li   x6,  0
    srl  x7,  x5, x6
    li   x6,  1
    srl  x7,  x5, x6
    srli x7,  x5, 1
    li   x6,  2
    srl  x7,  x5, x6
    li   x6,  4
    srl  x7,  x5, x6
    li   x6,  8
    srl  x7,  x5, x6
    li   x6,  16
    srl  x7,  x5, x6
    srli x7,  x5, 16

    # --- Arithmetic shift-right: MSB=1 fill-ones, MSB=0 fill-zeros ---
    li   x5,  0x80000000
    li   x6,  1
    sra  x7,  x5, x6
    srai x7,  x5, 1
    li   x6,  2
    sra  x7,  x5, x6
    li   x6,  4
    sra  x7,  x5, x6
    li   x6,  8
    sra  x7,  x5, x6
    li   x6,  16
    sra  x7,  x5, x6
    srai x7,  x5, 16
    li   x5,  0x7FFFFFFF
    li   x6,  1
    sra  x7,  x5, x6
    srai x7,  x5, 4

    # --- Signed/unsigned compare, including opposite-sign SLT ---
    li   x5,  1
    li   x6,  2
    slt  x7,  x5, x6
    slt  x7,  x6, x5
    sltu x7,  x5, x6
    slti x7,  x5, 8
    sltiu x7, x5, 8
    li   x5,  0xFFFFFFFF
    li   x6,  1
    slt  x7,  x5, x6
    slt  x7,  x6, x5
    sltu x7,  x5, x6

    # --- LSU widths + signed/unsigned loads (dport rd/wr) ---
    lb   x7,  0(x10)
    lb   x7,  1(x10)
    lb   x7,  2(x10)
    lb   x7,  3(x10)
    lbu  x7,  0(x10)
    lbu  x7,  1(x10)
    lh   x7,  0(x10)
    lh   x7,  2(x10)
    lhu  x7,  0(x10)
    lhu  x7,  2(x10)
    lw   x7,  0(x10)
    sb   x6,  0(x10)
    sb   x6,  1(x10)
    sh   x6,  2(x10)
    sw   x6,  4(x10)

    # --- Branches: taken and not-taken, signed and unsigned ---
    li   x8,  1
    li   x9,  1
    beq  x8,  x9, 1f
    nop
1:
    bne  x8,  x9, 2f
    nop
2:
    li   x9,  3
    beq  x8,  x9, 3f
    nop
3:
    bne  x8,  x9, 4f
    nop
4:
    li   x8,  0xFFFFFFFF
    li   x9,  1
    blt  x8,  x9, 5f
    nop
5:
    bge  x8,  x9, 6f
    nop
6:
    bltu x8,  x9, 7f
    nop
7:
    bgeu x8,  x9, 8f
    nop
8:
    li   x8,  5
    li   x9,  1
    blt  x8,  x9, 9f
    nop
9:
    bge  x8,  x9, 10f
    nop
10:
    jal  x1,  11f
    nop
11:
    auipc x12, 0
    la   x13, 12f
    jalr x1,  x13, 0
    nop
12:

    # --- M extension ---
    li   x5,  7
    li   x6,  3
    mul  x7,  x5, x6
    mulh x7,  x5, x6
    mulhu x7, x5, x6
    mulhsu x7, x5, x6
    div  x7,  x5, x6
    divu x7,  x5, x6
    rem  x7,  x5, x6
    remu x7,  x5, x6
    li   x5,  0x80000000
    li   x6,  0xFFFFFFFF
    mulh x7,  x5, x6
    div  x7,  x5, x6

    # Halt mailbox at 0xF000 (TCM)
    li   x5,  0xF000
    li   x6,  1
    sw   x6,  0(x5)
halt:
    j    halt

    .section .data
    .align 4
scratch:
    .space 64
    .section .tohost,"aw",@progbits
    .align 4
    .globl tohost
tohost: .word 0
