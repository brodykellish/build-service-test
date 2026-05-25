# Sample Dockerfile for the RunPod build service end-to-end example.
# manual_e2e.py reads this file verbatim and sends its contents to the
# build service as an inline build source. Edit it freely; the only
# load-bearing string is the GREETING marker below, which manual_e2e.py
# compares against the container's stdout to confirm the bytes that came
# back are the bytes we built.

FROM alpine:3.20

# Appears in the streamed build logs you'll see in your terminal.
RUN echo "hello from the RunPod build service"

# Stamp the image with a known marker. If you change this string, also
# update the GREETING constant in manual_e2e.py.
RUN echo "HELLO_FROM_RUNPOD_BUILD_SERVICE" > /greeting.txt
CMD ["cat", "/greeting.txt"]
