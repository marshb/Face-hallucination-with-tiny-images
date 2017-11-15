# coding: utf-8

import tensorflow as tf
from tqdm import tqdm
import numpy as np
import inputpipe as ip
import glob, os
from argparse import ArgumentParser
import utils, config
from PIL import Image

os.environ["CUDA_VISIBLE_DEVICES"] = '3'

def build_parser():
    parser = ArgumentParser()
    parser.add_argument('--num_epochs', default=20, help='default: 20', type=int)
    parser.add_argument('--batch_size', default=128, help='default: 128', type=int)
    parser.add_argument('--num_threads', default=4, help='# of data read threads (default: 4)', type=int)
    models_str = ' / '.join(config.model_zoo)
    parser.add_argument('--model', help=models_str, required=True) # DRAGAN, CramerGAN
    parser.add_argument('--name', help='default: name=model')
    parser.add_argument('--dataset', help='CelebA / LSUN', required=True)
    parser.add_argument('--renew', action='store_true', help='train model from scratch - \
        clean saved checkpoints and summaries', default=False)
    # more arguments: dataset

    return parser


def input_pipeline(glob_pattern, batch_size, num_threads, num_epochs):
    tfrecords_list = glob.glob(glob_pattern)
    # num_examples = utils.num_examples_from_tfrecords(tfrecords_list) # takes too long time for lsun
    X, sample_z = ip.shuffle_batch_join(tfrecords_list, batch_size=batch_size, num_threads=num_threads, num_epochs=num_epochs)
    return X, sample_z


def sample_z(shape):
    return np.random.uniform(-0.1,0.1,size=shape)



def train(model, input_op, num_epochs, batch_size, n_examples, renew=False):
    # n_examples = 202599 # same as util.num_examples_from_tfrecords(glob.glob('./data/celebA_tfrecords/*.tfrecord'))
    # 1 epoch = 1583 steps
    print("\n# of examples: {}".format(n_examples))
    print("steps per epoch: {}\n".format(n_examples//batch_size))

    summary_path = os.path.join('./summary/', model.name)
    ckpt_path = os.path.join('./checkpoints', model.name)
    if renew:
        if os.path.exists(summary_path):
            tf.gfile.DeleteRecursively(summary_path)
        if os.path.exists(ckpt_path):
            tf.gfile.DeleteRecursively(ckpt_path)
    if not os.path.exists(ckpt_path):
        tf.gfile.MakeDirs(ckpt_path)

    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True

     # Works same as CUDA_VISIBLE_DEVICES!
    with tf.Session(config=config) as sess:
        sess.run(tf.global_variables_initializer())
        sess.run(tf.local_variables_initializer()) # for epochs

        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(coord=coord)


        config_list = [('batch_size', batch_size), ('dataset', FLAGS.dataset)]
        model_config_list = [[k, str(w)] for k, w in sorted(model.args.items()) + config_list]
        model_config_summary_op = tf.summary.text('config', tf.convert_to_tensor(model_config_list), collections=[]) #change the text into scaler as my tensorflow version is r1.0
        model_config_summary = sess.run(model_config_summary_op)

        summary_writer = tf.summary.FileWriter(summary_path, flush_secs=30, graph=sess.graph)
        summary_writer.add_summary(model_config_summary)
        total_steps = int(np.ceil(n_examples * num_epochs / float(batch_size))) # total global step
        pbar = tqdm(total=total_steps, desc='global_step')


        saver = tf.train.Saver(max_to_keep=1000) # save all checkpoints
        global_step = 0

        ckpt = tf.train.get_checkpoint_state(ckpt_path)
        if ckpt:
            saver.restore(sess, ckpt.model_checkpoint_path)
            global_step = sess.run(model.global_step)
            print('\nRestore from {} ... starting global step is {}\n'.format(ckpt.model_checkpoint_path, global_step))
            pbar.update(global_step)

        try:
            idx = 0
            while not coord.should_stop():
                # model.all_summary_op contains histogram summary and image summary which are heavy op
                summary_op = model.summary_op if global_step % 100 == 0 else model.all_summary_op

                batch_X, batch_z = sess.run(input_op)
                z_noise = sample_z(batch_z.shape)
                batch_z += z_noise

                # save_image_lr = (batch_z + 1.) * 127.5
                # for i in range(len(save_image_lr)) :
                #     idx += 1
                #     img = np.array(save_image_lr[i], dtype=np.uint8)
                #     img = Image.fromarray(img)
                #     img.save('tmp/{}.jpg'.format(idx))
                # continue
                _, summary, global_step = sess.run([model.D_train_op, summary_op, model.global_step], feed_dict={model.X: batch_X, model.z: batch_z})

                _ = sess.run([model.G_train_op], feed_dict={model.X: batch_X, model.z: batch_z})

                summary_writer.add_summary(summary, global_step=global_step)

                if global_step % 10 == 0:
                    pbar.update(10)

                    if global_step % 1000 == 0:
                        saver.save(sess, ckpt_path+'/'+model.name, global_step=global_step)

        except tf.errors.OutOfRangeError:
            print('\nDone -- epoch limit reached\n')
        finally:
            coord.request_stop()

        coord.join(threads)
        summary_writer.close()
        pbar.close()


if __name__ == "__main__":
    parser = build_parser()
    FLAGS = parser.parse_args()
    FLAGS.model = FLAGS.model.upper()
    FLAGS.dataset = FLAGS.dataset.lower()
    if FLAGS.name is None:
        FLAGS.name = FLAGS.model.lower()
    config.pprint_args(FLAGS)

    # get information for dataset
    dataset_pattern, n_examples = config.get_dataset(FLAGS.dataset)

    # input pipeline
    X = input_pipeline(dataset_pattern, batch_size=FLAGS.batch_size,
        num_threads=FLAGS.num_threads, num_epochs=FLAGS.num_epochs)
    model = config.get_model(FLAGS.model, FLAGS.name, training=True)
    train(model=model, input_op=X, num_epochs=FLAGS.num_epochs, batch_size=FLAGS.batch_size,
        n_examples=n_examples, renew=FLAGS.renew)