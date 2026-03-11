extern "C" {

#include <stdint.h>
#include <string.h>

#define ROTRIGHT(a,b) (((a) >> (b)) | ((a) << (32-(b))))
#define CH(x,y,z) (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define EP0(x) (ROTRIGHT(x,2) ^ ROTRIGHT(x,13) ^ ROTRIGHT(x,22))
#define EP1(x) (ROTRIGHT(x,6) ^ ROTRIGHT(x,11) ^ ROTRIGHT(x,25))
#define SIG0(x) (ROTRIGHT(x,7) ^ ROTRIGHT(x,18) ^ ((x) >> 3))
#define SIG1(x) (ROTRIGHT(x,17) ^ ROTRIGHT(x,19) ^ ((x) >> 10))

typedef unsigned char BYTE;
typedef uint32_t  WORD;

typedef struct {
    BYTE data[64];
    WORD datalen;
    unsigned long long bitlen;
    WORD state[8];
} SHA256_CTX;

__constant__ WORD dev_k[64];

__device__ __forceinline__ void sha256_transform(SHA256_CTX* ctx, const BYTE data[])
{
    WORD a,b,c,d,e,f,g,h,i,j,t1,t2,m[64];

    #pragma unroll 16
    for (i=0,j=0; i<16; ++i, j+=4)
        m[i] = ( (WORD)data[j] << 24 ) | ( (WORD)data[j+1] << 16 ) | ( (WORD)data[j+2] << 8 ) | (WORD)data[j+3];

    #pragma unroll 64
    for (; i<64; ++i)
        m[i] = SIG1(m[i-2]) + m[i-7] + SIG0(m[i-15]) + m[i-16];

    a = ctx->state[0]; b = ctx->state[1]; c = ctx->state[2]; d = ctx->state[3];
    e = ctx->state[4]; f = ctx->state[5]; g = ctx->state[6]; h = ctx->state[7];

    #pragma unroll 64
    for (i=0; i<64; ++i) {
        t1 = h + EP1(e) + CH(e,f,g) + dev_k[i] + m[i];
        t2 = EP0(a) + MAJ(a,b,c);
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }

    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

__device__ __forceinline__ void sha256_init(SHA256_CTX* ctx)
{
    ctx->datalen = 0;
    ctx->bitlen = 0;
    ctx->state[0]=0x6a09e667; ctx->state[1]=0xbb67ae85; ctx->state[2]=0x3c6ef372; ctx->state[3]=0xa54ff53a;
    ctx->state[4]=0x510e527f; ctx->state[5]=0x9b05688c; ctx->state[6]=0x1f83d9ab; ctx->state[7]=0x5be0cd19;
}

__device__ __forceinline__ void sha256_update(SHA256_CTX* ctx, const BYTE data[], size_t len)
{
    for (size_t i=0; i<len; ++i) {
        ctx->data[ctx->datalen] = data[i];
        if (++ctx->datalen == 64) {
            sha256_transform(ctx, ctx->data);
            ctx->bitlen += 512;
            ctx->datalen = 0;
        }
    }
}

__device__ __forceinline__ void sha256_final(SHA256_CTX* ctx, BYTE hash[])
{
    WORD i = ctx->datalen;

    if (ctx->datalen < 56) {
        ctx->data[i++] = 0x80;
        while (i < 56) ctx->data[i++] = 0;
    } else {
        ctx->data[i++] = 0x80;
        while (i < 64) ctx->data[i++] = 0;
        sha256_transform(ctx, ctx->data);
        for (i=0; i<56; ++i) ctx->data[i] = 0;
    }

    ctx->bitlen += (unsigned long long)ctx->datalen * 8ull;
    ctx->data[63] = (BYTE)(ctx->bitlen      );
    ctx->data[62] = (BYTE)(ctx->bitlen >>  8);
    ctx->data[61] = (BYTE)(ctx->bitlen >> 16);
    ctx->data[60] = (BYTE)(ctx->bitlen >> 24);
    ctx->data[59] = (BYTE)(ctx->bitlen >> 32);
    ctx->data[58] = (BYTE)(ctx->bitlen >> 40);
    ctx->data[57] = (BYTE)(ctx->bitlen >> 48);
    ctx->data[56] = (BYTE)(ctx->bitlen >> 56);
    sha256_transform(ctx, ctx->data);

    #pragma unroll
    for (i=0; i<4; ++i) {
        hash[i     ] = (ctx->state[0] >> (24 - i*8)) & 0xff;
        hash[i +  4] = (ctx->state[1] >> (24 - i*8)) & 0xff;
        hash[i +  8] = (ctx->state[2] >> (24 - i*8)) & 0xff;
        hash[i + 12] = (ctx->state[3] >> (24 - i*8)) & 0xff;
        hash[i + 16] = (ctx->state[4] >> (24 - i*8)) & 0xff;
        hash[i + 20] = (ctx->state[5] >> (24 - i*8)) & 0xff;
        hash[i + 24] = (ctx->state[6] >> (24 - i*8)) & 0xff;
        hash[i + 28] = (ctx->state[7] >> (24 - i*8)) & 0xff;
    }
}

// Uppercase hex encoding for hash comparison
__device__ __forceinline__ void sha256_to_hex_uc(const unsigned char* data, char* out64)
{
    const char hex[16] = { '0','1','2','3','4','5','6','7','8','9','A','B','C','D','E','F' };
    #pragma unroll
    for (int i=0; i<32; ++i) {
        out64[i*2    ] = hex[(data[i] >> 4) & 0xF];
        out64[i*2 + 1] = hex[(data[i]     ) & 0xF];
    }
}

__device__ __forceinline__ bool nibble_prefix_match(const char* hh, const unsigned char* chunk, unsigned len)
{
    #pragma unroll
    for (unsigned i=0; i<len; ++i) { if (hh[i] != (char)chunk[i]) return false; }
    return true;
}

__device__ __forceinline__ bool bytes_contains_uc(const unsigned char* arr, size_t n, unsigned char v)
{
    #pragma unroll
    for (size_t i=0; i<n; ++i) { if (arr[i] == v) return true; }
    return false;
}

// Miner kernel for single-host-worker scenario.
// Nonce stepping:
//   start_offset = 0 for single worker
//   global_step  = blocks * threads
//   base_offset  = batch_idx * iters_per_thread * global_step
// Each thread starts at: i = start_offset + tid + base_offset
// and advances by global_step per iteration.
__global__ void miner_kernel(
    const unsigned char* __restrict__ hash_prefix,
    size_t prefix_len,
    const unsigned char* __restrict__ last_chunk,
    unsigned idiff,
    const unsigned char* __restrict__ charset,
    unsigned charset_len,
    unsigned int* __restrict__ result,     // 0xFFFFFFFF initially; set to nonce if found
    uint32_t start_offset,
    uint32_t global_step,
    uint32_t base_offset,
    uint32_t iters_per_thread
) {
    uint32_t tid = blockIdx.x * blockDim.x + threadIdx.x;
    uint32_t i = start_offset + tid + base_offset;

    const size_t temp_size = prefix_len + 4;
    unsigned char temp[320];
    unsigned char digest[32];
    char hexhash[64];

    for (size_t k=0; k<prefix_len; ++k) temp[k] = hash_prefix[k];
    unsigned char* nonce_ptr = temp + prefix_len;

    for (uint32_t it=0; it<iters_per_thread; ++it) {
        if (*result != 0xFFFFFFFFu) return; // another thread found a result

        memcpy(nonce_ptr, &i, 4);

        SHA256_CTX ctx;
        sha256_init(&ctx);
        sha256_update(&ctx, temp, temp_size);
        sha256_final(&ctx, digest);

        sha256_to_hex_uc(digest, hexhash);

        if ((idiff == 0 || nibble_prefix_match(hexhash, last_chunk, idiff)) &&
            (charset_len == 16 || bytes_contains_uc(charset, charset_len, (unsigned char)hexhash[idiff])))
        {
            atomicCAS(result, 0xFFFFFFFFu, i);
            return;
        }

        i += global_step;
    }
}

} // extern "C"

