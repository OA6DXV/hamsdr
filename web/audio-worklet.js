class RadioAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(32768);
    this.write = 0; this.read = 0; this.started = false; this.blocks = 0;
    this.port.onmessage = ({data}) => {
      if (data.reset) {this.read = this.write; this.started = false; return;}
      const view = new DataView(data);
      for (let i = 0; i + 1 < view.byteLength; i += 2) this.buffer[(this.write++) % this.buffer.length] = view.getInt16(i, true) / 32768;
      // Bound latency to half a second; stale samples are never replayed.
      if (this.write - this.read > 8000) {this.read = this.write - 1600; this.started = false;}
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!this.started && this.write-this.read >= 1600) this.started = true;
    let power = 0;
    const buffered = this.write - this.read;
    // Small clock correction maintains the buffer despite SDR/audio-clock drift.
    const correction = Math.max(-0.004, Math.min(0.004, (buffered-2000)*0.000003));
    const step = 16000/sampleRate*(1+correction);
    for (let i=0; i<out.length; i++) {
      if (!this.started || this.read+1 >= this.write) {out[i]=0; this.started=false; continue;}
      const n=Math.floor(this.read), f=this.read-n;
      out[i]=this.buffer[n%this.buffer.length]*(1-f)+this.buffer[(n+1)%this.buffer.length]*f;
      this.read+=step; power+=out[i]*out[i];
    }
    if (++this.blocks%100 === 0) this.port.postMessage({rms:Math.sqrt(power/out.length),buffered:this.write-this.read});
    return true;
  }
}
registerProcessor('radio-audio',RadioAudio);
